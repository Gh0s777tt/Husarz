"""Zapisywalny magazyn sekretów Husarza — szyfrowany plik pod referencją ``husarz:``.

**Po co istnieje.** Dotychczasowi dostawcy sekretów (:mod:`husarz.config.secrets`) są
WYŁĄCZNIE do odczytu: operator sam umieszcza materiał w ENV, pliku, Vaulcie albo SOPS-ie,
a konfiguracja niesie jedynie referencję. To dobry model i on się nie zmienia — ale nie
obsługuje przypadku, w którym to Husarz **otrzymuje** sekret w czasie działania: token
wklejony w kreatorze połączeń albo zwrócony przez OAuth. Bez zapisywalnego magazynu taki
token musiałby wylądować w pliku konfiguracji (złamanie zasady „config nie zawiera
materiału") albo w pamięci procesu (utrata po restarcie).

**Jak zachowuje niezmiennik.** Magazyn zapisuje materiał ZASZYFROWANY w osobnym pliku, a
wołającemu zwraca referencję ``husarz:<nazwa>``. Do konfiguracji i do magazynu połączeń Git
trafia wyłącznie ta referencja — dokładnie tak, jak dziś trafia tam ``env:``/``vault:``.
Model „config nie zawiera materiału" zostaje nienaruszony, a magazyn dokłada się do
istniejącego łańcucha jako kolejny :class:`~husarz.config.secrets.SecretsProvider`.

**Model zagrożeń — co ten magazyn chroni, a czego NIE.**

- Chroni przed odczytem pliku przez inne konto na maszynie: plik ``0600`` w katalogu ``0700``,
  treść zaszyfrowana AES-256-GCM, ``AAD`` = nazwa wpisu (anti-swap — szyfrogramu nie da się
  podstawić pod inną nazwę bez unieważnienia tagu).
- Chroni przed wyciekiem przez kopię zapasową i przez przypadkowe wysłanie pliku: bez klucza
  głównego zawartość jest bezużyteczna.
- **NIE chroni** przed kimś, kto ma jednocześnie plik magazynu i klucz główny. Klucz główny
  pochodzi z referencji rozwiązywanej przez zwykłego dostawcę (``env:``/``file:``/``vault:``/
  ``sops:``), więc siła całości równa się sile ochrony tego klucza. Trzymanie klucza w Vaulcie
  daje realną separację; trzymanie go w ENV obok pliku magazynu daje głównie ochronę
  kopii zapasowych. To ograniczenie jest świadome i udokumentowane, nie przeoczone.
- **NIE chroni** przed procesem działającym na koncie operatora — sekret jest z definicji
  odszyfrowywalny przez sam Husarz.

**Dlaczego nie system keychain.** Byłby mocniejszy, ale wiąże się z platformą (Keychain/DPAPI/
Secret Service) i wypadałby w kontenerze oraz w trybie airgap na serwerze bez sesji graficznej.
Plik + klucz z dostawcy działa wszędzie tak samo i pozostaje audytowalny.
"""

from __future__ import annotations

import base64
import contextlib
import json
import os
import re
import threading
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from husarz.core.crypto import AesGcmCipher, Cipher, derive_key
from husarz.core.errors import CryptoError
from husarz.core.filelock import FileLockError, blokada_pliku
from husarz.security.errors import SecurityError

SCHEME = "husarz:"
"""Schemat referencji obsługiwany przez ten magazyn."""

_FORMAT_VERSION = 1
# Nazwa wpisu: bezpieczna dla klucza JSON i dla oka operatora; ukośnik pozwala grupować
# (``git/github``), ale NIE jest ścieżką w systemie plików — magazyn to jeden plik.
_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._/-]{0,127}$")


class SecretStoreError(SecurityError):
    """Błąd magazynu sekretów (walidacja nazwy, odczyt/zapis pliku, szyfrowanie)."""


def _default_clock() -> datetime:
    return datetime.now(UTC)


@contextlib.contextmanager
def _blokada_pliku(sciezka: Path) -> Iterator[None]:
    """Wyłączna blokada międzyprocesowa na czas modyfikacji magazynu.

    Cienka obwoluta na :func:`husarz.core.filelock.blokada_pliku`. Prymityw wyprowadzono do
    warstwy 0, bo tej samej blokady potrzebuje dziennik audytu — a dziennik nie ma powodu
    importować magazynu sekretów. Obwoluta zostaje, żeby zachować kontrakt tego modułu:
    wołający łapie ``SecretStoreError`` i nie musi wiedzieć, skąd wzięto prymityw.

    Args:
        sciezka: Ścieżka pliku magazynu (plik blokady powstanie obok, z sufiksem ``.lock``).

    Yields:
        Nic — blokada obowiązuje w obrębie bloku ``with``.

    Raises:
        SecretStoreError: Gdy pliku blokady nie da się utworzyć albo zablokować.
    """
    try:
        with blokada_pliku(sciezka):
            yield
    except FileLockError as exc:
        raise SecretStoreError(str(exc)) from exc


class EncryptedFileSecretStore:
    """Szyfrowany magazyn sekretów w jednym pliku JSON.

    Implementuje protokół :class:`~husarz.config.secrets.SecretsProvider` (metoda
    :meth:`resolve`), więc wpina się w istniejący łańcuch dostawców bez zmian w rdzeniu.

    Args:
        path: Ścieżka pliku magazynu. Katalog nadrzędny powstanie z prawami ``0700``.
        cipher: Szyfr do zapieczętowania wartości. W praktyce :class:`AesGcmCipher`
            zbudowany przez :func:`build_secret_store`.
        clock: Wstrzykiwalne źródło czasu (testowalność znaczników ``created_at``).

    Raises:
        SecretStoreError: Gdy istniejący plik magazynu jest nieczytelny lub uszkodzony.
    """

    def __init__(
        self,
        path: str | Path,
        cipher: Cipher,
        *,
        clock: Callable[[], datetime] = _default_clock,
    ) -> None:
        self._path = Path(path)
        self._cipher = cipher
        self._clock = clock
        # Serializuje mutację + zapis: endpointy FastAPI biegną w puli wątków.
        self._lock = threading.Lock()
        self._entries: dict[str, dict[str, Any]] = {}
        # Znacznik wersji pliku — pozwala wykryć zapis dokonany przez INNY proces.
        self._znacznik: tuple[float, int] | None = None
        self._load()
        self._znacznik = self._stan_pliku()

    def _modyfikuj(
        self,
        zmiana: Callable[
            [
                dict[str, dict[str, Any]],
            ],
            dict[str, dict[str, Any]],
        ],
    ) -> None:
        """Odczyt-modyfikacja-zapis magazynu POD BLOKADĄ międzyprocesową.

        Kolejność ma trzy powody, każdy z osobnej awarii:

        1. **Blokada NAJPIERW** — bez niej dwa procesy nadpisywały sobie zapisy, bo każdy
           startował od SWOJEJ kopii wpisów. Token zapisany przez jeden znikał po zapisie
           drugiego, bez żadnego błędu.
        2. **Ponowny odczyt z dysku pod blokadą** — kopia w pamięci mogła się zestarzeć,
           odkąd inny proces coś dopisał. Modyfikujemy stan FAKTYCZNY, nie zapamiętany.
        3. **Zapis przed podmianą stanu w pamięci** — nieudany zapis nie może zostawić
           procesu z przekonaniem, że sekret istnieje, podczas gdy w pliku go nie ma.

        Args:
            zmiana: Funkcja czysta: bierze wpisy z dysku, zwraca wpisy do zapisania.

        Raises:
            SecretStoreError: Gdy odczyt albo zapis się nie powiedzie.
        """
        with self._lock, _blokada_pliku(self._path):
            self._load()
            kandydat = zmiana(self._entries)
            self._persist(kandydat)
            self._entries = kandydat
            self._znacznik = self._stan_pliku()

    def _stan_pliku(self) -> tuple[float, int] | None:
        """Znacznik wersji pliku (czas modyfikacji, rozmiar) albo ``None``, gdy pliku brak."""
        try:
            st = self._path.stat()
        except OSError:
            return None
        return (st.st_mtime, st.st_size)

    def _przeladuj_jesli_zmieniony(self) -> None:
        """Dociąga wpisy, gdy plik zmienił się od ostatniego odczytu (inny proces).

        Bez tego kopia w pamięci starzeje się w milczeniu: sekret dopisany przez drugi proces
        byłby dla tego procesu nieistniejący, więc połączenie utworzone tam nie działałoby tu.
        Sprawdzenie kosztuje jedno ``stat`` — tanio jak na operację i tak dotykającą dysku.
        """
        biezacy = self._stan_pliku()
        if biezacy == self._znacznik:
            return
        with self._lock:
            self._load()
            self._znacznik = biezacy

    # ------------------------------------------------------------------ odczyt

    def resolve(self, ref: str) -> str | None:
        """Rozwiązuje referencję ``husarz:<nazwa>`` do wartości sekretu.

        Fail-closed i CICHO: nieznana nazwa, obcy schemat albo nieudane odszyfrowanie dają
        ``None``, nie wyjątek. Wyjątek niósłby informację, KTÓRE referencje istnieją, a
        komunikat mógłby trafić do logu — dostawcy sekretów w tym projekcie zachowują się
        tak samo (por. ``VaultSecretsProvider``).

        Args:
            ref: Referencja, np. ``husarz:git/github``.

        Returns:
            Wartość sekretu albo ``None``, gdy referencja nie należy do tego magazynu,
            nie istnieje lub nie da się jej odszyfrować.
        """
        if not ref.startswith(SCHEME):
            return None
        self._przeladuj_jesli_zmieniony()
        name = ref[len(SCHEME) :]
        entry = self._entries.get(name)
        if entry is None:
            return None
        try:
            sealed = base64.b64decode(entry["sealed"], validate=True)
            plaintext = self._cipher.unseal(sealed, aad=name.encode("utf-8"))
        except (CryptoError, ValueError, KeyError, TypeError):
            # Zły klucz główny, uszkodzony plik albo podmieniony wpis — fail-closed.
            return None
        return plaintext.decode("utf-8")

    def names(self) -> list[str]:
        """Zwraca posortowane nazwy wpisów. NIE ujawnia wartości ani szyfrogramów."""
        self._przeladuj_jesli_zmieniony()
        return sorted(self._entries)

    def describe(self, name: str) -> dict[str, str] | None:
        """Metadane wpisu (nazwa, znacznik utworzenia) — bez wartości i bez szyfrogramu.

        Args:
            name: Nazwa wpisu.

        Returns:
            Słownik ``{"name": ..., "created_at": ...}`` albo ``None``, gdy wpisu brak.
        """
        self._przeladuj_jesli_zmieniony()
        entry = self._entries.get(name)
        if entry is None:
            return None
        return {"name": name, "created_at": str(entry.get("created_at", ""))}

    # ------------------------------------------------------------------- zapis

    def put(self, name: str, value: str) -> str:
        """Zapisuje (albo nadpisuje) sekret i zwraca referencję ``husarz:<nazwa>``.

        Args:
            name: Nazwa wpisu (``[a-zA-Z0-9._/-]``, do 128 znaków).
            value: Materiał sekretu. Pusty jest odrzucany — cichy pusty sekret objawiłby się
                dopiero jako nieautoryzowane żądanie do zdalnego serwisu.

        Returns:
            Referencja do użycia w konfiguracji i w magazynie połączeń.

        Raises:
            SecretStoreError: Gdy nazwa jest niepoprawna, wartość pusta albo zapis się nie uda.
        """
        self._validate_name(name)
        if not value or not value.strip():
            raise SecretStoreError("Pusta wartość sekretu — odmawiam zapisu.")
        try:
            sealed = self._cipher.seal(value.encode("utf-8"), aad=name.encode("utf-8"))
        except CryptoError as exc:
            # Komunikat prymitywu nie zawiera materiału, ale nazwy wpisu też nie dokładamy
            # do niczego, co mogłoby trafić dalej niż do operatora.
            raise SecretStoreError(f"Nie można zaszyfrować sekretu: {exc}") from exc
        wpis = {
            "sealed": base64.b64encode(sealed).decode("ascii"),
            "created_at": self._clock().isoformat(),
        }
        self._modyfikuj(lambda entries: {**entries, name: wpis})
        return f"{SCHEME}{name}"

    def delete(self, name: str) -> bool:
        """Usuwa wpis. Idempotentne.

        Args:
            name: Nazwa wpisu.

        Returns:
            ``True``, gdy wpis istniał i został usunięty; ``False``, gdy go nie było.

        Raises:
            SecretStoreError: Gdy zapis pliku się nie uda.
        """
        istnial = False

        def bez_wpisu(entries: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
            nonlocal istnial
            istnial = name in entries
            return {k: v for k, v in entries.items() if k != name}

        self._modyfikuj(bez_wpisu)
        return istnial

    # ---------------------------------------------------------------- wewnętrzne

    @staticmethod
    def _validate_name(name: str) -> None:
        """Sprawdza nazwę wpisu; nazwa trafia do JSON-a i do referencji w configu."""
        if not _NAME_RE.match(name):
            raise SecretStoreError(
                "Niepoprawna nazwa sekretu — dozwolone są litery, cyfry oraz '.', '_', '-', '/', "
                "pierwszy znak musi być alfanumeryczny, długość do 128 znaków."
            )
        if ".." in name:
            # Nazwa nie jest ścieżką, ale '..' w referencji w configu myli przy przeglądzie.
            raise SecretStoreError("Nazwa sekretu nie może zawierać '..'.")

    def _load(self) -> None:
        """Wczytuje magazyn z dysku. Brak pliku = pusty magazyn (nie błąd)."""
        if not self._path.exists():
            return
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            entries = data["entries"]
            if not isinstance(entries, dict):
                raise TypeError("pole 'entries' nie jest obiektem")
            self._entries = {str(k): dict(v) for k, v in entries.items()}
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
            # Fail-closed: uszkodzonego magazynu NIE traktujemy jak pustego, bo cichy start
            # z pustym magazynem wyglądałby jak „token wygasł", a nie jak awaria pliku.
            raise SecretStoreError(
                f"Nie można wczytać magazynu sekretów z {self._path}: {exc}"
            ) from exc

    def _persist(self, entries: dict[str, dict[str, Any]]) -> None:
        """Zapisuje podane wpisy atomowo i TRWALE, z prawami ``0600`` od chwili powstania.

        Plik tymczasowy powstaje przez ``os.open`` z jawnym trybem, a nie przez
        ``write_text``: inaczej istniałoby okno, w którym szyfrogramy leżą z domyślnymi
        prawami (na wielu systemach czytelnymi dla grupy).

        **Trzy poziomy gwarancji, każdy potrzebny z innego powodu.** ``os.replace`` daje
        atomowość wobec CZYTELNIKA — nikt nie zobaczy połowy zapisu. To jednak nie to samo
        co trwałość wobec AWARII ZASILANIA: bez ``fsync`` dane mogą jeszcze siedzieć
        w buforze systemu, a po nagłym restarcie plik bywa pusty albo obcięty — czyli
        magazyn sekretów staje się nieczytelny i (fail-closed) blokuje start aplikacji.
        Dlatego synchronizujemy najpierw PLIK, a po podmianie także KATALOG: sama zawartość
        pliku nie wystarcza, gdy w buforze zostaje jeszcze wpis katalogowy wskazujący na
        nową nazwę.

        Pełna pętla ``os.write`` jest konieczna, bo zapis krótszy niż całość jest legalny
        (na zwykłym pliku rzadki, ale kontrakt tego nie gwarantuje) — cichy zapis połowy
        JSON-a zniszczyłby WSZYSTKIE sekrety naraz, nie tylko bieżący.

        Args:
            entries: Wpisy do utrwalenia. Świadomie parametr, a nie ``self._entries``:
                stan w pamięci podmieniamy dopiero PO udanym zapisie.

        Raises:
            SecretStoreError: Gdy zapis się nie powiedzie.
        """
        payload = {"version": _FORMAT_VERSION, "entries": entries}
        blob = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        self._path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        tmp = self._path.with_name(f"{self._path.name}.{os.getpid()}.tmp")
        try:
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            try:
                zapisane = 0
                while zapisane < len(blob):
                    zapisane += os.write(fd, blob[zapisane:])
                os.fsync(fd)
            finally:
                os.close(fd)
            os.replace(tmp, self._path)
            self._fsync_katalogu()
        except OSError as exc:
            # Sprzątanie nie może SAMO rzucić: `unlink(missing_ok=True)` tłumi wyłącznie
            # FileNotFoundError, a gdy katalog nadrzędny nie istnieje (albo jest plikiem),
            # leci NotADirectoryError — i wymykał się tej obsłudze, przesłaniając
            # właściwą przyczynę awarii.
            with contextlib.suppress(OSError):
                tmp.unlink(missing_ok=True)
            raise SecretStoreError(
                f"Nie można zapisać magazynu sekretów do {self._path}: {exc}"
            ) from exc

    def _fsync_katalogu(self) -> None:
        """Synchronizuje wpis katalogowy po ``os.replace``.

        Nieobsługiwane na części systemów (m.in. Windows) — tam brak synchronizacji katalogu
        NIE jest błędem i nie może wywrócić zapisu, który się powiódł.
        """
        try:
            fd = os.open(self._path.parent, os.O_RDONLY)
        except OSError:  # pragma: no cover - zależy od systemu plików
            return
        try:
            os.fsync(fd)
        except OSError:  # pragma: no cover - Windows nie pozwala fsync na katalogu
            pass
        finally:
            os.close(fd)


def build_secret_store(
    *,
    path: str | Path,
    key_ref: str | None,
    secrets: Any = None,
    clock: Callable[[], datetime] = _default_clock,
) -> EncryptedFileSecretStore:
    """Buduje magazyn, wyprowadzając klucz główny z referencji do sekretu.

    Fail-closed: magazyn bez rozwiązywalnego klucza NIE powstaje. Nie ma trybu „zapisz
    jawnie" — taki tryb byłby najgorszym z możliwych, bo plik z tokenami wyglądałby
    identycznie niezależnie od tego, czy cokolwiek chroni jego zawartość.

    Args:
        path: Ścieżka pliku magazynu.
        key_ref: Referencja do klucza głównego (``env:``/``file:``/``vault:``/``sops:``).
        secrets: Dostawca sekretów rozwiązujący ``key_ref``.
        clock: Wstrzykiwalne źródło czasu.

    Returns:
        Gotowy magazyn.

    Raises:
        SecretStoreError: Gdy brak ``key_ref``, gdy nie da się go rozwiązać albo gdy brakuje
            biblioteki ``cryptography``. Ostatni przypadek wykrywa konstruktor
            :class:`~husarz.core.crypto.AesGcmCipher` — magazyn NIE powstanie bez
            działającego backendu, więc nie da się dojść do stanu, w którym wygląda on na
            sprawny, a pierwszy zapis tokenu zawodzi.
    """
    if not key_ref:
        raise SecretStoreError(
            "Magazyn sekretów wymaga referencji do klucza głównego (secret_store.key_ref)."
        )
    material = secrets.resolve(key_ref) if secrets is not None else None
    if not material or not material.strip():
        raise SecretStoreError(
            f"Nie udało się rozwiązać klucza magazynu sekretów ('{key_ref}') — fail-closed."
        )
    try:
        cipher = AesGcmCipher(derive_key(material))
    except CryptoError as exc:
        raise SecretStoreError(f"Nie można zbudować szyfru magazynu sekretów: {exc}") from exc
    return EncryptedFileSecretStore(path, cipher, clock=clock)
