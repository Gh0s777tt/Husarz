"""Niemodyfikowalny dziennik audytu z łańcuchem skrótów (tamper-evidence).

Każdy wpis zawiera skrót ``sha256(prev_hash + kanoniczny_payload)``. Zmiana
dowolnego wcześniejszego wpisu unieważnia wszystkie kolejne skróty, więc
``verify`` wykrywa manipulację. Zapis jest tylko dopisujący (append-only).
Zegar jest wstrzykiwalny — testy są deterministyczne.
"""

from __future__ import annotations

import copy
import hashlib
import hmac
import json
import os
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from husarz.config.schema import AuditConfig, SecurityConfig
from husarz.config.secrets import SecretsProvider
from husarz.core.filelock import FileLockError, blokada_pliku
from husarz.security.errors import AuditError

GENESIS_HASH = "0" * 64


def _default_clock() -> datetime:
    return datetime.now(UTC)


@dataclass(slots=True, frozen=True)
class AuditEntry:
    """Pojedynczy, niezmienny wpis audytu."""

    timestamp: str
    actor: str
    action: str
    detail: dict[str, Any]
    roe_ref: str | None
    prev_hash: str
    entry_hash: str
    # KTO zlecił akcję (referencja konta/tokenu, nie nazwa użytkownika — bez PII w logu,
    # który jest niemodyfikowalny). Puste = brak uwierzytelnienia albo wpis sprzed Etapu 13b.
    principal: str = ""
    # Etykieta POKOLENIA klucza HMAC, którym policzono `entry_hash`. Puste = pokolenie
    # sprzed pierwszej rotacji (albo dziennik bez HMAC). Pole jest częścią payloadu tylko
    # wtedy, gdy jest niepuste — dokładnie jak `principal`, i z tego samego powodu.
    key_id: str = ""


def _payload(
    timestamp: str,
    actor: str,
    action: str,
    detail: dict[str, Any],
    roe_ref: str | None,
    prev_hash: str,
    principal: str = "",
    key_id: str = "",
) -> str:
    """Buduje kanoniczny payload wpisu do policzenia skrótu łańcucha.

    ``principal`` trafia do payloadu WYŁĄCZNIE, gdy jest niepusty. To celowe i ma dwie
    konsekwencje, obie pożądane:

    1. **Zgodność wstecz** — dzienniki sprzed dodania tego pola hashują się dokładnie tak
       jak wcześniej, więc ``verify`` na starym pliku nadal przechodzi (inaczej dodanie
       kolumny wyglądałoby jak manipulacja całą historią).
    2. **Tamper-evidence bez luki** — dopisanie albo usunięcie ``principal`` w istniejącym
       wpisie zmienia payload, więc skrót przestaje pasować. Nie da się „odpiąć" wywołania
       od użytkownika ani podpiąć go pod kogo innego bez wykrycia.

    ``key_id`` podlega tej samej regule i z tych samych powodów. Objęcie go payloadem NIE
    wystarcza jednak samo w sobie: posiadacz WYCOFANEGO klucza mógłby przecież przeliczyć
    wpisy, oznaczając je swoim (wciąż akceptowanym) pokoleniem. Przed tym chroni dopiero
    reguła niemalejącego pokolenia w ``AuditLog.verify`` — patrz jej docstring.
    """
    data: dict[str, Any] = {
        "timestamp": timestamp,
        "actor": actor,
        "action": action,
        "detail": detail,
        "roe_ref": roe_ref,
        "prev_hash": prev_hash,
    }
    if principal:
        data["principal"] = principal
    if key_id:
        data["key_id"] = key_id
    return json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


@dataclass(slots=True)
class AuditLog:
    """Dopisujący dziennik audytu z weryfikowalnym łańcuchem skrótów.

    Bez ``hmac_key`` łańcuch (goły SHA-256) jest tamper-evident wobec przypadkowej
    korekty. Z ``hmac_key`` (klucz spoza logu) staje się odporny także na
    zmotywowanego edytora bez klucza — zalecane w produkcji.

    **Łańcuch NIE wykrywa odcięcia OGONA.** Usunięcie ostatnich linii pliku zostawia
    prefiks, który jest wewnętrznie spójny — ``verify()`` zwracało ``True``, choć wpisy
    zniknęły. Odtworzone pomiarem: dziennik z pięcioma wpisami po usunięciu dwóch ostatnich
    nadal przechodził weryfikację. Dla dziennika opisywanego jako „niemodyfikowalny" to
    poważna luka: najłatwiejszym sposobem zatarcia śladu jest właśnie usunięcie końcówki,
    a nie edycja w środku.

    Stąd **kotwica** (``anchor_path``): plik obok dziennika, w którym trzymamy liczbę wpisów
    i skrót ostatniego z nich. Przy wczytaniu porównujemy — brakujące wpisy albo przepisana
    historia stają się widoczne.

    Czego kotwica NIE daje (mówimy to wprost): kto ma prawo zapisu do katalogu dziennika,
    ten ma je też do kotwicy. **Poprzeczka jest przy tym niższa, niż mówiło pierwsze
    sformułowanie** („usuń linie i zaktualizuj kotwicę"): ``_kompletny_wobec_kotwicy``
    traktuje BRAK kotwicy jak zgodność, a usunięcie pliku jest łatwiejsze od podrobienia go
    i wymaga tych samych uprawnień, co usunięcie linii dziennika. Faktycznie więc: „usuń
    linie i usuń kotwicę". Utratę kontroli widać w ``stan_kotwicy`` (i w ``GET /api/audit``),
    ale to sygnał dla operatora, a nie przeszkoda dla napastnika. Kotwica wykrywa przede
    wszystkim awarie PRZYPADKOWE (urwany zapis, nieudana rotacja) i podnosi koszt ataku;
    nie zastępuje ``hmac_key`` trzymanego POZA systemem plików ani nadzoru zewnętrznego.
    Te mechanizmy są komplementarne.
    """

    path: Path | None = None
    clock: Callable[[], datetime] = _default_clock
    hmac_key: bytes | None = None
    # Etykieta pokolenia klucza BIEŻĄCEGO — trafia do nowych wpisów.
    key_id: str = ""
    # Klucze wcześniejszych pokoleń: (etykieta, materiał), OD NAJSTARSZEGO. Służą wyłącznie
    # do weryfikacji historii; nowe wpisy podpisuje zawsze `hmac_key`.
    verify_keys: list[tuple[str, bytes]] = field(default_factory=list)
    # Ścieżka kotwicy. ``None`` = bez kotwicy (zachowanie sprzed jej wprowadzenia).
    anchor_path: Path | None = None
    _entries: list[AuditEntry] = field(default_factory=list)
    _last_hash: str = GENESIS_HASH
    # Rozmiar pliku w bajtach, jaki ten proces widział ostatnio. Różnica względem stanu
    # faktycznego znaczy, że dopisał ktoś INNY — i że nasza głowa łańcucha jest nieaktualna.
    _rozmiar_znany: int = 0
    # Serializuje dopisywanie: endpointy FastAPI (zwykłe ``def``) biegną w puli
    # wątków, więc read-modify-write łańcucha skrótów musi być atomowe — inaczej
    # dwa równoległe wpisy dostają ten sam ``prev_hash`` i ``verify`` daje fałszywy
    # alarm manipulacji. Wykluczony z porównań/reprezentacji dataclass.
    _lock: threading.Lock = field(default_factory=threading.Lock, compare=False, repr=False)

    def _skrot(self, payload: str, klucz: bytes | None) -> str:
        """Liczy skrót payloadu wskazanym kluczem (``None`` = goły SHA-256)."""
        data = payload.encode("utf-8")
        if klucz is not None:
            return hmac.new(klucz, data, hashlib.sha256).hexdigest()
        return hashlib.sha256(data).hexdigest()

    def _pokolenia(self) -> list[tuple[str, bytes]]:
        """Pokolenia kluczy od NAJSTARSZEGO; ostatnie jest bieżące. Puste = tryb bez HMAC."""
        if self.hmac_key is None:
            return []
        return [*self.verify_keys, (self.key_id, self.hmac_key)]

    def _pokolenie_wpisu(self, key_id: str) -> tuple[int, bytes | None] | None:
        """Dobiera pokolenie i klucz dla etykiety wpisu.

        Args:
            key_id: Etykieta zapisana we wpisie.

        Returns:
            Para ``(indeks pokolenia, klucz)`` albo ``None``, gdy etykieta jest NIEZNANA.
            ``None`` musi prowadzić do niepowodzenia weryfikacji: wpis, którego nie mamy
            czym sprawdzić, jest wpisem niesprawdzonym, a nie wpisem poprawnym.
        """
        pokolenia = self._pokolenia()
        if not pokolenia:
            # Tryb bez HMAC: pokolenie jest jedno, a o wszystkim rozstrzyga zgodność skrótu.
            return (0, None)
        for indeks, (etykieta, klucz) in enumerate(pokolenia):
            if etykieta == key_id:
                return (indeks, klucz)
        return None

    @property
    def entries(self) -> list[AuditEntry]:
        """Kopia listy wpisów (tylko do odczytu z zewnątrz)."""
        return list(self._entries)

    @property
    def head_hash(self) -> str:
        """Skrót ostatniego wpisu (lub genesis, gdy pusty)."""
        return self._last_hash

    def record(
        self,
        actor: str,
        action: str,
        detail: dict[str, Any] | None = None,
        *,
        roe_ref: str | None = None,
        principal: str = "",
    ) -> AuditEntry:
        """Dopisuje wpis i zwraca go. Zapis do pliku PRZED mutacją pamięci.

        Args:
            actor: kto WYKONAŁ (agent albo ``api``).
            action: nazwa zdarzenia.
            detail: szczegóły (bez surowej treści/sekretów — skróty i rozmiary).
            roe_ref: identyfikator zlecenia ROE, jeśli dotyczy.
            principal: kto ZLECIŁ (referencja konta/tokenu). Rozdzielenie „kto wykonał"
                od „kto zlecił" jest istotą rozliczalności przy wielu użytkownikach —
                sam ``actor='kopijnik'`` nie odpowiada na pytanie, czyje to było żądanie.
        """
        # Głęboka kopia — treść nie może zmienić się po zahashowaniu (niezmienność).
        safe_detail = copy.deepcopy(dict(detail or {}))
        # Cała sekwencja (odczyt prev_hash → hash → zapis pliku → mutacja pamięci)
        # jest sekcją krytyczną: gwarantuje ciągłość łańcucha pod współbieżnością.
        #
        # Dwie blokady, bo są dwa różne wyścigi. `self._lock` (wątkowy) obsługuje pulę
        # wątków FastAPI. Blokada PLIKOWA obsługuje drugi PROCES — i sama nie wystarcza:
        # proces trzyma `_last_hash` w pamięci, więc pod blokadą musi jeszcze sprawdzić,
        # czy plik nie urósł. Bez tego kroku zapisuje na podstawie głowy sprzed blokady.
        with self._lock, self._blokada_zapisu():
            self._odswiez_z_pliku()
            timestamp = self.clock().isoformat()
            try:
                payload = _payload(
                    timestamp,
                    actor,
                    action,
                    safe_detail,
                    roe_ref,
                    self._last_hash,
                    principal,
                    self.key_id,
                )
            except (TypeError, ValueError) as exc:
                raise AuditError(f"Szczegóły audytu nie są serializowalne do JSON: {exc}") from exc
            entry = AuditEntry(
                timestamp=timestamp,
                actor=actor,
                action=action,
                detail=safe_detail,
                roe_ref=roe_ref,
                prev_hash=self._last_hash,
                # Nowe wpisy podpisuje ZAWSZE klucz bieżący. Klucze historyczne są
                # wyłącznie do czytania — inaczej rotacja nie odcinałaby niczego.
                entry_hash=self._skrot(payload, self.hmac_key),
                principal=principal,
                key_id=self.key_id,
            )
            # Persist-first: jeśli zapis pliku zawiedzie, stan w pamięci NIE rozjeżdża się.
            self._append_to_file(entry)
            self._entries.append(entry)
            self._last_hash = entry.entry_hash
            # Kotwica PO wpisie, nie przed. Kolejność jest istotna dla awarii: przerwanie
            # między jednym a drugim zostawia dziennik O KROK DO PRZODU względem kotwicy,
            # co jest stanem bezpiecznym (nic nie zginęło). Odwrotna kolejność zostawiałaby
            # kotwicę wskazującą na wpis, którego nie ma — czyli fałszywy alarm manipulacji
            # po zwykłym zaniku zasilania.
            self._zapisz_kotwice()
        return entry

    @contextmanager
    def _blokada_zapisu(self) -> Iterator[None]:
        """Blokada MIĘDZYPROCESOWA na czas dopisywania. Bez pliku — brak blokady.

        Yields:
            Nic — blokada obowiązuje w obrębie bloku ``with``.

        Raises:
            AuditError: Gdy blokady nie da się zająć. Dopisanie bez niej byłoby gorsze niż
                błąd: rozgałęziony łańcuch wygląda jak manipulacja i unieważnia dziennik.
        """
        if self.path is None:
            yield
            return
        try:
            with blokada_pliku(self.path):
                yield
        except FileLockError as exc:
            raise AuditError(f"Nie można zająć blokady dziennika audytu: {exc}") from exc

    def _wczytaj(self, source: Path) -> None:
        """Wczytuje CAŁY dziennik z pliku, zastępując stan w pamięci.

        Stan podmieniamy DOPIERO po udanym wczytaniu całości. Wcześniejsza wersja czyściła
        ``_entries`` przed pętlą, więc wyjątek w połowie zostawiał dziennik załadowany
        częściowo — a w trybie ``warn`` proces szedł z takim stanem dalej i dopisywał do
        łańcucha urwanego w przypadkowym miejscu.

        Args:
            source: Ścieżka pliku JSONL.

        Raises:
            OSError: Gdy pliku nie da się odczytać.
            ValueError: Gdy linia nie jest poprawnym JSON-em.
            TypeError: Gdy linia jest poprawnym JSON-em, ale nie wpisem audytu (brakujące
                albo nadmiarowe pola). Wołający MUSI go łapać — inaczej jedna wadliwa linia
                daje niekontrolowany crash zamiast czytelnej odmowy.
        """
        tresc = source.read_text(encoding="utf-8")
        wpisy: list[AuditEntry] = []
        for line in tresc.splitlines():
            if not line.strip():
                continue
            wpisy.append(AuditEntry(**json.loads(line)))
        self._entries = wpisy
        self._last_hash = wpisy[-1].entry_hash if wpisy else GENESIS_HASH
        self._rozmiar_znany = len(tresc.encode("utf-8"))

    def _odswiez_z_pliku(self) -> None:
        """Wczytuje dziennik ponownie, gdy plik urósł — i ODMAWIA, gdy się skurczył.

        **Po co ponowny odczyt.** Dwa procesy Husarza wskazujące ten sam plik dopisywały
        równolegle, każdy ze swoim ``_last_hash`` z chwili wczytania. Skutkiem był łańcuch
        ROZGAŁĘZIONY: w realnym dzienniku tego projektu wpis nr 261 wskazywał na skrót wpisu
        nr 256, pomijając cztery wpisy zapisane w międzyczasie przez drugi proces.

        **Po co ODMOWA — i dlaczego to nie jest ostrożność na wyrost.** Pierwsza wersja tej
        metody przyjmowała KAŻDĄ zmianę rozmiaru jako „dopisał ktoś inny". Plik wyłącznie
        dopisujący nie może się jednak skurczyć: skurcz jest sygnałem odcięcia ogona albo
        rotacji pliku, a nie zdarzeniem do zaakceptowania. Skutek był poważny i odwracał
        sens kotwicy z Etapu 17n — odtworzony pomiarem:

        * dziennik ma 5 wpisów, kotwica mówi ``{"wpisow": 5}``,
        * napastnik obcina PLIK do 2 wpisów (kotwicy nie rusza) — ``verify()`` słusznie
          zwraca ``False``,
        * ofiara wykonuje JEDEN zwykły wpis audytu (np. logowanie),
        * ten wpis wczytuje obcięty plik jako prawdę, dopisuje się na jego końcu,
          a ``_zapisz_kotwice`` przepisuje kotwicę na ``{"wpisow": 3}``,
        * od tej chwili ``verify()`` zwraca ``True``, a trzy wpisy zniknęły bez śladu.

        Ofiara własnymi rękami zacierała jedyny dowód. Dlatego skurcz jest tu twardą
        odmową, a kotwica dostała osobno zapadkę (patrz ``_zapisz_kotwice``) — dwie
        niezależne kontrole, bo jedna z nich kiedyś zawiodła.

        Raises:
            AuditError: Gdy plik zniknął, skurczył się, przestał zawierać dotychczasową
                głowę łańcucha albo nie daje się odczytać.
        """
        if self.path is None:
            return
        if not self.path.is_file():
            # Zniknięcie pliku przy niepustym stanie to logrotate albo usunięcie. Dopisanie
            # wtedy założyłoby NOWY plik zaczynający się w środku łańcucha — dokument
            # wyglądający na kompletny, a pozbawiony początku.
            if self._entries:
                raise AuditError(
                    f"Dziennik audytu {self.path} ZNIKNĄŁ w trakcie pracy (rotacja pliku, "
                    f"przeniesienie albo usunięcie), a proces ma w pamięci "
                    f"{len(self._entries)} wpis(ów). Dopisanie założyłoby nowy plik "
                    f"zaczynający się w środku łańcucha. Zatrzymaj Husarza, zabezpiecz to, "
                    f"co zostało, i uruchom go ponownie."
                )
            return
        try:
            rozmiar = self.path.stat().st_size
            if rozmiar == self._rozmiar_znany:
                return
            if rozmiar < self._rozmiar_znany:
                raise AuditError(
                    f"Dziennik audytu {self.path} SKURCZYŁ SIĘ w trakcie pracy "
                    f"({self._rozmiar_znany} -> {rozmiar} bajtów). Dziennik jest wyłącznie "
                    f"dopisujący, więc to nie jest stan, który mógłby powstać normalną "
                    f"pracą: albo ktoś odciął ogon, albo plik przeszedł rotację. "
                    f"Dopisanie w tym miejscu ZATARŁOBY ślad, więc odmawiamy."
                )
            poprzednia_glowa = self._last_hash
            self._wczytaj(self.path)
            if poprzednia_glowa != GENESIS_HASH and all(
                wpis.entry_hash != poprzednia_glowa for wpis in self._entries
            ):
                raise AuditError(
                    f"Dziennik audytu {self.path} urósł, ale NIE zawiera już wpisu, który "
                    f"ten proces zapisał jako ostatni. Historia została przepisana — plik "
                    f"większy nie znaczy plik uzupełniony. Odmawiamy dopisania."
                )
        except (OSError, ValueError, TypeError) as exc:
            raise AuditError(
                f"Nie można ponownie odczytać dziennika audytu {self.path}: {exc}"
            ) from exc

    def _zapisz_kotwice(self) -> None:
        """Utrwala liczbę wpisów i skrót ostatniego — poza plikiem dziennika.

        Błąd zapisu kotwicy NIE przerywa audytu: wpis jest już bezpiecznie na dysku,
        a kotwica to warstwa dodatkowa. Przerwanie działania w tym miejscu zamieniłoby
        ulepszenie wykrywalności w nową awarię ścieżki krytycznej.
        """
        if self.anchor_path is None:
            return
        wpisow = len(self._entries)
        if not self._kotwica_moze_isc_w_gore(wpisow):
            return
        try:
            self.anchor_path.parent.mkdir(parents=True, exist_ok=True)
            tymczasowy = self.anchor_path.with_suffix(self.anchor_path.suffix + ".tmp")
            with tymczasowy.open("w", encoding="utf-8") as uchwyt:
                uchwyt.write(json.dumps({"wpisow": wpisow, "skrot": self._last_hash}))
                uchwyt.flush()
                # Bez fsync kotwica mogła zostać w buforze, podczas gdy WPIS już zszedł
                # na nośnik — po zaniku zasilania zostawałaby kotwica sprzed wpisu albo
                # plik pusty, czyli fałszywy alarm manipulacji po zwykłej awarii zasilania.
                os.fsync(uchwyt.fileno())
            # Podmiana atomowa: kotwica nigdy nie jest widziana w stanie połowicznym.
            tymczasowy.replace(self.anchor_path)
        except OSError:  # pragma: no cover - rzadka ścieżka I/O
            return

    def _kotwica_moze_isc_w_gore(self, wpisow: int) -> bool:
        """Zapadka kotwicy: liczba wpisów nigdy nie maleje.

        **Dlaczego zapadka, skoro skurcz pliku jest już odrzucany.** Bo to dwie NIEZALEŻNE
        kontrole tego samego zdarzenia, a jedna z nich raz już zawiodła. Kotwica jest
        jedynym dowodem odcięcia ogona (Etap 17n); dopóki `_zapisz_kotwice` zapisywało
        `len(self._entries)` bezwarunkowo, DOWOLNA ścieżka kończąca się mniejszą liczbą
        wpisów w pamięci cicho ją cofała. Skurcz pliku był tylko pierwszą taką ścieżką,
        jaką znaleziono — zapadka zamyka wszystkie następne, także te jeszcze niepowstałe.

        Nieczytelnej kotwicy nie traktujemy jak zakazu zapisu: to by znaczyło, że
        uszkodzenie pliku pomocniczego trwale zamraża kontrolę kompletności.

        Args:
            wpisow: Liczba wpisów, którą chcemy zapisać.

        Returns:
            ``True``, gdy zapis jest dozwolony (kotwica rośnie albo jej nie ma).
        """
        if self.anchor_path is None or not self.anchor_path.is_file():
            return True
        try:
            dotychczas = int(json.loads(self.anchor_path.read_text(encoding="utf-8"))["wpisow"])
        except (OSError, ValueError, KeyError, TypeError):
            return True
        return wpisow >= dotychczas

    def verify(self) -> bool:
        """Sprawdza integralność dziennika: łańcuch skrótów ORAZ kompletność wobec kotwicy.

        Sam łańcuch nie wystarcza — patrz docstring klasy: odcięcie ogona zostawia prefiks
        wewnętrznie spójny, więc do wprowadzenia kotwicy ta metoda meldowała „brak
        manipulacji" na dzienniku, z którego usunięto wpisy.

        **Reguła niemalejącego pokolenia.** Wpisy niosą etykietę pokolenia klucza
        (``key_id``), a pokolenia są uporządkowane od najstarszego. Idąc po łańcuchu,
        indeks pokolenia nie może ZMALEĆ. Bez tej reguły rotacja byłaby pozorna: kto zdobył
        klucz WYCOFANY, mógłby dopisać albo przepisać końcówkę dziennika, oznaczając własne
        wpisy starym pokoleniem — a ono nadal jest akceptowane, bo służy do czytania
        historii. Z regułą taki wpis jest odrzucany, gdy tylko w dzienniku istnieje
        cokolwiek nowszego. Dlatego ``build_audit_log`` zapisuje znacznik rotacji od razu
        po wymianie klucza: zamyka okno, w którym nowszego wpisu jeszcze nie ma.

        Czego reguła NIE daje — trzy zastrzeżenia, każde istotne:

        1. Dopóki cały dziennik należy do jednego pokolenia, chroni go wyłącznie klucz tego
           pokolenia. Rotacja zabezpiecza to, co po niej, a nie to, co przed nią.
        2. Reguła broni przed DOPISANIEM za wpisami nowszego pokolenia, ale nie przed ich
           USUNIĘCIEM. Napastnik, który ma prawo zapisu do pliku (a musi je mieć, żeby w
           ogóle dopisywać), może cofnąć dziennik do swojej ery i dopiero wtedy dopisywać.
           Przy DZIAŁAJĄCYM procesie skurczenie pliku jest wykrywane i odrzucane
           (``_odswiez_z_pliku``), a kotwica ma zapadkę — ale przed zimnym startem na już
           spreparowanym pliku chroni dopiero nadzór ZEWNĘTRZNY.
        3. Kotwica, czyli jedyna kontrola kompletności, nie jest uwierzytelniona i jej BRAK
           jest traktowany jak zgodność. Widać to w ``stan_kotwicy``, ale to sygnał dla
           operatora, nie przeszkoda dla napastnika.

        Wpis z etykietą NIEZNANĄ (nie ma takiego klucza w konfiguracji) unieważnia
        weryfikację. „Nie mam czym sprawdzić” nigdy nie zaokrągla się do „w porządku”.

        Returns:
            ``True``, gdy łańcuch jest spójny, pokolenia nie cofają się i nic nie zniknęło.
        """
        if not self._odswiezony_do_weryfikacji():
            return False
        if not self._kompletny_wobec_kotwicy():
            return False
        prev = GENESIS_HASH
        najstarsze_dozwolone = 0
        for entry in self._entries:
            if entry.prev_hash != prev:
                return False
            pokolenie = self._pokolenie_wpisu(entry.key_id)
            if pokolenie is None:
                return False
            indeks, klucz = pokolenie
            if indeks < najstarsze_dozwolone:
                return False
            najstarsze_dozwolone = indeks
            payload = _payload(
                entry.timestamp,
                entry.actor,
                entry.action,
                entry.detail,
                entry.roe_ref,
                prev,
                entry.principal,
                entry.key_id,
            )
            if self._skrot(payload, klucz) != entry.entry_hash:
                return False
            prev = entry.entry_hash
        return True

    def _odswiezony_do_weryfikacji(self) -> bool:
        """Dociąga stan z dysku przed weryfikacją. ``False`` = plik mówi coś niepokojącego.

        **Po co.** ``verify()`` sprawdzało wyłącznie stan w PAMIĘCI, a stan w pamięci
        aktualizował dotąd tylko ``record()``. Dla działającego procesu znaczyło to, że
        ``GET /api/audit`` raportowałby ``verified: true`` także wtedy, gdy plik na dysku
        został w międzyczasie obcięty — bo obiekt nadal trzymał komplet wpisów. Jedynym
        punktem wykrycia był restart, czyli w praktyce: nic.

        Dziennik wczytany do wglądu (``load``) nie ma ścieżki, więc nie ma czego dociągać —
        weryfikuje dokładnie to, co mu podano.

        Returns:
            ``True``, gdy stan jest aktualny; ``False``, gdy plik skurczył się, zniknął albo
            przestał być czytelny — czyli gdy weryfikacja i tak nie może wypaść pomyślnie.
        """
        if self.path is None:
            return True
        try:
            self._odswiez_z_pliku()
        except AuditError:
            return False
        return True

    def stan_kotwicy(self) -> str:
        """Czy kontrola kompletności w ogóle DZIAŁA — do pokazania operatorowi.

        Kotwica jest jedynym mechanizmem wykrywającym odcięcie ogona (Etap 17n), a jej brak
        albo uszkodzenie po prostu ją WYŁĄCZA: ``_kompletny_wobec_kotwicy`` zwraca wtedy
        ``True``, żeby uszkodzenie pliku pomocniczego nie unieważniało dziennika. Ta decyzja
        jest słuszna, ale miała wadę — była NIEWIDOCZNA. Ta sama instalacja, która przy
        nieczytelnym DZIENNIKU odmawia startu, przy nieczytelnej KOTWICY milczała zupełnie:
        żadnego komunikatu, żadnego pola w ``GET /api/audit``. Operator nie miał jak się
        dowiedzieć, że kontrola przestała działać — czyli dostawał „fałszywe OK".

        Rozdzielamy brak od uszkodzenia, bo znaczą co innego: ``brak`` to dziennik sprzed
        wprowadzenia kotwicy ALBO jej usunięcie (usunąć jest łatwiej niż podrobić, więc
        faktyczna poprzeczka dla napastnika to „usuń linie i usuń kotwicę"), a
        ``nieczytelna`` to zwykle urwany zapis.

        Returns:
            ``wylaczona`` (dziennik bez kotwicy), ``brak``, ``nieczytelna`` albo ``ok``.
        """
        if self.anchor_path is None:
            return "wylaczona"
        if not self.anchor_path.is_file():
            return "brak"
        try:
            dane = json.loads(self.anchor_path.read_text(encoding="utf-8"))
            int(dane["wpisow"])
            str(dane["skrot"])
        except (OSError, ValueError, KeyError, TypeError):
            return "nieczytelna"
        return "ok"

    def _kompletny_wobec_kotwicy(self) -> bool:
        """Czy dziennik zawiera wszystko, co kotwica widziała ostatnio.

        Trzy rozstrzygnięcia, każde z uzasadnieniem:

        * **mniej wpisów niż w kotwicy** → ODCIĘCIE. Wpisy zniknęły.
        * **tyle samo lub więcej, ale skrót na pozycji kotwicy się nie zgadza** → historia
          została PRZEPISANA. Sama liczba wpisów by tego nie złapała: napastnik mógłby
          usunąć końcówkę i dopisać własną o tej samej długości.
        * **więcej wpisów, skrót się zgadza** → w porządku. Dziennik wyprzedza kotwicę,
          co zdarza się po przerwaniu między zapisem wpisu a zapisem kotwicy — nic nie zginęło.

        Returns:
            ``True``, gdy brak kotwicy albo gdy dziennik jest wobec niej kompletny.
        """
        if self.anchor_path is None or not self.anchor_path.is_file():
            return True
        try:
            dane = json.loads(self.anchor_path.read_text(encoding="utf-8"))
            oczekiwane = int(dane["wpisow"])
            skrot = str(dane["skrot"])
        except (OSError, ValueError, KeyError, TypeError):
            # Nieczytelna kotwica NIE może unieważniać dziennika — to byłby fałszywy alarm
            # manipulacji po zwykłym uszkodzeniu pliku pomocniczego.
            return True
        if len(self._entries) < oczekiwane:
            return False
        if oczekiwane == 0:
            return True
        return self._entries[oczekiwane - 1].entry_hash == skrot

    @classmethod
    def load(
        cls,
        path: str | Path,
        *,
        hmac_key: bytes | None = None,
        key_id: str = "",
        verify_keys: list[tuple[str, bytes]] | None = None,
        anchor_path: str | Path | None = None,
    ) -> AuditLog:
        """Wczytuje dziennik z pliku JSONL i odtwarza łańcuch (do ``verify``).

        Args:
            path: Ścieżka pliku JSONL.
            hmac_key: Klucz pokolenia BIEŻĄCEGO albo ``None`` (tryb gołego SHA-256).
            key_id: Etykieta pokolenia bieżącego.
            verify_keys: Klucze pokoleń wcześniejszych, OD NAJSTARSZEGO.
            anchor_path: Ścieżka kotwicy; ``None`` = weryfikacja bez kontroli kompletności.

        Returns:
            Dziennik gotowy do ``verify`` (bez ścieżki zapisu — wczytany jest tylko do odczytu).
        """
        log = cls(
            path=None,
            hmac_key=hmac_key,
            key_id=key_id,
            verify_keys=list(verify_keys or []),
            anchor_path=Path(anchor_path) if anchor_path is not None else None,
        )
        log._wczytaj(Path(path))
        return log

    def _append_to_file(self, entry: AuditEntry) -> None:
        """Dopisuje wpis do pliku i wymusza jego zejście na nośnik.

        ``fsync`` nie jest ostrożnością na wyrost. ``record`` obiecuje semantykę
        „persist-first": stan w pamięci zmienia się dopiero PO udanym zapisie. Bez
        wymuszenia bufory systemu tę obietnicę unieważniają — po zaniku zasilania wpis,
        który proces uznał za utrwalony, po prostu nie istnieje. Dla dziennika, którego
        jedynym zadaniem jest rozliczalność, to nie jest kompromis wydajnościowy.

        Args:
            entry: Wpis do dopisania.

        Raises:
            AuditError: Gdy zapis się nie powiedzie.
        """
        if self.path is None:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(asdict(entry), ensure_ascii=False) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            self._rozmiar_znany = self.path.stat().st_size
        except OSError as exc:  # pragma: no cover - rzadka ścieżka I/O
            raise AuditError(f"Nie można dopisać do audytu: {exc}") from exc


def build_audit_log(
    security: SecurityConfig, *, secrets: SecretsProvider | None = None
) -> AuditLog:
    """Buduje dziennik audytu z konfiguracji (ścieżka z ``security.audit``).

    Gdy plik już istnieje, odtwarza z niego łańcuch — dopiski po restarcie procesu
    pozostają ciągłe (bez fałszywego genesis w środku pliku).

    **Klucz HMAC (``audit.hmac_key_ref``) zmienia charakter dziennika.** Bez niego łańcuch
    to goły SHA-256: kto ma prawo zapisu do pliku, może przeliczyć go od nowa i podmienić
    historię tak, że ``verify()`` niczego nie zauważy — kotwica podnosi poprzeczkę, ale nie
    zamyka tej drogi. Z kluczem trzymanym POZA systemem plików przekucie łańcucha staje się
    niewykonalne bez tego klucza.

    **Przy włączonym HMAC start jest FAIL-CLOSED.** Jeśli istniejący dziennik nie weryfikuje
    się kluczem, odmawiamy startu. Nie da się bowiem odróżnić „plik powstał, zanim włączono
    HMAC" od „ktoś bez klucza przepisał historię" — a milcząca degradacja do trybu bez klucza
    zniweczyłaby cały sens jego włączania. Operator ma zarchiwizować stary dziennik i zacząć
    nowy; komunikat mówi to wprost.

    Bez klucza zachowanie pozostaje bez zmian: uszkodzony łańcuch nie blokuje startu, tylko
    jest widoczny jako ``verified: false`` w `GET /api/audit`. To świadome rozróżnienie —
    skonfigurowanie klucza jest deklaracją „integralność tego dziennika jest blokująca".

    Args:
        security: Konfiguracja bezpieczeństwa.
        secrets: Dostawca sekretów do rozwiązania ``hmac_key_ref``. Wymagany, gdy referencja
            jest ustawiona — brak dostawcy jest błędem konfiguracji, nie cichym pominięciem.

    Returns:
        Gotowy dziennik.

    Raises:
        AuditError: Gdy klucza nie da się rozwiązać albo istniejący dziennik nie weryfikuje
            się tym kluczem.
    """
    audit = security.audit
    if not audit.enabled:
        return AuditLog(path=None)
    hmac_key = _rozwiaz_klucz_hmac(audit.hmac_key_ref, secrets)
    verify_keys: list[tuple[str, bytes]] = []
    for pokolenie in audit.hmac_verify_keys:
        klucz = _rozwiaz_klucz_hmac(pokolenie.ref, secrets)
        if klucz is None:  # pragma: no cover - `ref` jest wymagane i niepuste (walidacja)
            raise AuditError(
                f"Nie udało się rozwiązać historycznego klucza HMAC audytu "
                f"(`{pokolenie.ref}`, pokolenie '{pokolenie.id}')."
            )
        verify_keys.append((pokolenie.id, klucz))
    # Dwa pokolenia o TYM SAMYM materiale klucza znoszą regułę niemalejącego pokolenia:
    # wpis dałoby się „awansować" do nowszego pokolenia bez zmiany jego skrótu. Schemat tego
    # nie wychwyci, bo widzi wyłącznie referencje — dwie różne referencje mogą wskazywać ten
    # sam sekret. Sprawdzamy dopiero tutaj, gdy materiał jest rozwiązany.
    materialy = [*(k for _, k in verify_keys), *([hmac_key] if hmac_key is not None else [])]
    if len(set(materialy)) != len(materialy):
        raise AuditError(
            "Co najmniej dwa pokolenia klucza HMAC audytu wskazują TEN SAM materiał. "
            "Rotacja byłaby wtedy pozorna: ten sam klucz uwierzytelniałby wpisy stare "
            "i nowe, więc reguła niemalejącego pokolenia przestałaby cokolwiek odcinać. "
            "Sprawdź, czy referencje w `security.audit.hmac_key_ref` i `hmac_verify_keys` "
            "nie prowadzą do tego samego sekretu."
        )
    path = Path(audit.path)
    # Kotwica leży obok dziennika i ma tę samą nazwę z przyrostkiem. Osobny plik, bo musi
    # przetrwać odcięcie ogona dziennika — trzymanie jej W dzienniku niczego by nie dało.
    log = AuditLog(
        path=path,
        hmac_key=hmac_key,
        key_id=audit.hmac_key_id,
        verify_keys=verify_keys,
        anchor_path=path.with_name(path.name + ".kotwica"),
    )
    if path.exists():
        try:
            # Odczyt POD BLOKADĄ, tak samo jak zapis. Bez niej start w szczelinie między
            # dopisaniem wpisu a zapisaniem kotwicy przez INNY proces dawał parę
            # (plik starszy, kotwica nowsza) — czyli dokładnie warunek, który
            # `_kompletny_wobec_kotwicy` czyta jako ODCIĘCIE OGONA. Przy `integrity:
            # blocking` skutkiem byłaby odmowa startu z komunikatem sugerującym
            # manipulację: fałszywy alarm na ścieżce krytycznej, wprost przeciwny celowi
            # naprawy wyścigu. Blokada domyka szczelinę; kolejność odczytu przestaje mieć
            # znaczenie, bo w jej trakcie nikt nie dopisuje.
            with log._blokada_zapisu():
                log._wczytaj(path)
        except (OSError, ValueError, TypeError) as exc:
            # ODMOWA BEZWARUNKOWA — `integrity` tego nie dotyczy i nie może dotyczyć.
            #
            # Przełącznik `integrity` rozstrzyga, co robić, gdy łańcuch SIĘ NIE ZGADZA:
            # wtedy wiadomo, co w pliku jest, i można świadomie zdecydować, że instalacja
            # ma mimo to wstać. Tutaj sytuacja jest inna — pliku NIE DA SIĘ ODCZYTAĆ, więc
            # nie wiadomo, gdzie kończy się łańcuch. Dopisanie w takim stanie zaczyna nowy
            # genesis w ŚRODKU istniejącego pliku i skleja dwa łańcuchy w jeden dokument,
            # który wygląda na kompletny.
            #
            # `TypeError` jest tu równie ważny jak `ValueError`: `AuditEntry(**json.loads(l))`
            # rzuca właśnie jego, gdy linia jest poprawnym JSON-em, ale nie mapą wpisu
            # (`[1,2]`) albo ma nadmiarowe pole — czyli także wtedy, gdy dziennik zapisała
            # NOWSZA wersja Husarza. Bez niego wynikiem był surowy traceback zamiast odmowy,
            # a w runtime błąd nie był `AuditError`, więc API oddawało 500 zamiast 503.
            raise AuditError(
                f"Nie można odczytać dziennika audytu {path}: {exc}. Nie wiadomo więc, gdzie "
                f"kończy się łańcuch, a dopisanie zaczęłoby nowy genesis w środku "
                f"istniejącego pliku — powstałby dokument wyglądający na kompletny, a będący "
                f"zlepkiem dwóch łańcuchów. Odmowa NIE zależy od `security.audit.integrity`: "
                f"ten przełącznik rozstrzyga, co robić z łańcuchem, który się nie zgadza, "
                f"a nie z plikiem, którego nie da się przeczytać. Zbadaj plik i zarchiwizuj "
                f"go wraz z {path.name}.kotwica."
            ) from exc
        with log._blokada_zapisu():
            spojny = log.verify() if log.entries else True
        if not spojny and audit.wymusza_integralnosc:
            raise AuditError(_komunikat_odmowy(path, audit))
    # Znacznik rotacji POZA blokiem `path.exists()` i bez warunku „są jakieś wpisy".
    # Właśnie stan pusty jest tym, w którym okno rotacji stoi najszerzej otworem, a komunikat
    # odmowy startu sam prowadzi operatora prosto do niego („zarchiwizuj dziennik").
    _zapisz_znacznik_rotacji(log, audit)
    return log


def _komunikat_odmowy(path: Path, audit: AuditConfig) -> str:
    """Buduje komunikat odmowy startu — inny, gdy klucz HMAC jest, a inny gdy go nie ma.

    Rozróżnienie nie jest kosmetyczne: przy włączonym HMAC operator ma realny wybór
    (zarchiwizuj i zacznij od nowa), a bez klucza komunikat musi powiedzieć wprost, że
    kontrola wykrywa uszkodzenie, ale NIE odróżnia go od świadomej podmiany.

    Args:
        path: Ścieżka dziennika.
        audit: Konfiguracja audytu.

    Returns:
        Treść błędu po polsku.
    """
    if audit.hmac_key_ref is not None:
        return (
            f"Dziennik audytu {path} NIE weryfikuje się kluczami z "
            f"`security.audit.hmac_key_ref` / `hmac_verify_keys`. Możliwe przyczyny, "
            f"których NIE DA SIĘ od siebie odróżnić: plik powstał, zanim włączono HMAC; "
            f"klucz wymieniono bez wpisania poprzedniego do `hmac_verify_keys`; albo ktoś "
            f"przepisał historię. Milczące przejście w tryb bez klucza zniweczyłoby sens "
            f"jego włączenia, więc odmawiamy startu. Przy ROTACJI dopisz poprzedni klucz do "
            f"`security.audit.hmac_verify_keys` (etykieta pusta dla wpisów sprzed pierwszej "
            f"rotacji). W ostateczności zarchiwizuj dziennik wraz z {path.name}.kotwica."
        )
    return (
        f"Dziennik audytu {path} NIE weryfikuje się (`security.audit.integrity=blocking`). "
        f"Łańcuch skrótów albo kotwica nie zgadzają się z zawartością pliku. Bez klucza HMAC "
        f"kontrola wykrywa USZKODZENIE (urwany zapis, nieudana rotacja pliku), ale nie "
        f"odróżnia go od świadomej podmiany — bo każdy, kto ma prawo zapisu, może przeliczyć "
        f"goły SHA-256 od nowa. Zbadaj plik, zarchiwizuj go wraz z {path.name}.kotwica, a na "
        f"przyszłość ustaw `security.audit.hmac_key_ref`."
    )


def _zapisz_znacznik_rotacji(log: AuditLog, audit: AuditConfig) -> None:
    """Dopisuje wpis pokolenia BIEŻĄCEGO, gdy dziennik kończy się pokoleniem starszym.

    Zamyka okno, w którym rotacja jeszcze niczego nie chroni: dopóki w dzienniku nie ma
    ani jednego wpisu nowego pokolenia, reguła niemalejącego pokolenia nie ma się o co
    oprzeć, a posiadacz wycofanego klucza mógłby dopisywać do końcówki. Znacznik powstaje
    RAZ — po jego zapisaniu ostatnie pokolenie jest już bieżące.

    Args:
        log: Wczytany dziennik (już zweryfikowany).
        audit: Konfiguracja audytu.
    """
    if audit.hmac_key_ref is None:
        return
    if log.entries:
        poprzednie = log.entries[-1].key_id
        if poprzednie == audit.hmac_key_id:
            return
    else:
        # Dziennik PUSTY. Znacznik ma sens tylko wtedy, gdy rotacja została w ogóle
        # skonfigurowana — inaczej każda świeża instalacja zaczynałaby od wpisu o rotacji,
        # której nie było. Obecność kluczy historycznych jest tu jedynym dostępnym
        # świadectwem, że operator klucz wymieniał.
        if not audit.hmac_verify_keys:
            return
        poprzednie = ""
    log.record(
        "audit",
        "audit.key_rotated",
        # Wyłącznie ETYKIETY pokoleń — nazwy nadane przez operatora, nigdy materiał klucza.
        {"poprzednie_pokolenie": poprzednie, "biezace_pokolenie": audit.hmac_key_id},
    )


def _rozwiaz_klucz_hmac(ref: str | None, secrets: SecretsProvider | None) -> bytes | None:
    """Rozwiązuje referencję klucza HMAC. Fail-closed na każdym kroku.

    Args:
        ref: Referencja z konfiguracji albo ``None``.
        secrets: Dostawca sekretów.

    Returns:
        Klucz albo ``None``, gdy referencji nie podano.

    Raises:
        AuditError: Gdy referencja jest, a klucza nie da się uzyskać. Cicha praca BEZ klucza
            byłaby najgorszym wyjściem: operator myślałby, że dziennik jest chroniony.
    """
    if not ref:
        return None
    if secrets is None:
        raise AuditError(
            "security.audit.hmac_key_ref jest ustawione, ale nie przekazano dostawcy "
            "sekretów — klucza nie ma jak rozwiązać. To błąd złożenia aplikacji, nie "
            "konfiguracji operatora."
        )
    wartosc = secrets.resolve(ref)
    if not wartosc or not wartosc.strip():
        raise AuditError(
            f"Nie udało się rozwiązać klucza HMAC audytu (`{ref}`). Dziennik działałby wtedy "
            f"bez ochrony kluczem, a operator miałby prawo sądzić, że jest chroniony — "
            f"dlatego odmawiamy startu."
        )
    return wartosc.strip().encode("utf-8")
