"""Pobranie, weryfikacja podpisu i podmiana binarki Husarza.

**To jest kod najwrażliwszy w całym projekcie i tak go traktujemy.** Aktualizator pobiera
i doprowadza do WYKONANIA cudzego kodu. Bez weryfikacji podpisu przejęcie kanału wydań —
albo samego konta u dostawcy — dawałoby przejęcie każdej instalacji naraz. Dlatego:

* **podpis jest warunkiem, nie ostrzeżeniem.** Brak klucza, brak pliku podpisu albo podpis
  niepoprawny kończą się ODMOWĄ. Nie ma trybu „zainstaluj mimo wszystko";
* **weryfikacja poprzedza zapis na ścieżkę docelową.** Pobrane bajty żyją w pliku
  tymczasowym, dopóki podpis się nie zgodzi; plik oczekujący na podmianę nigdy nie leży
  pod nazwą, którą system uruchamia;
* **weryfikujemy DWA razy** — przy pobraniu i ponownie tuż przed podmianą. Między jednym
  a drugim mija restart, a przez ten czas plik leży na dysku operatora;
* **przekierowania obsługujemy RĘCZNIE.** Reszta projektu ustawia ``follow_redirects=False``,
  bo przekierowanie omija walidację i pin IP. Serwer wydań przekierowuje na magazyn plików,
  więc każdy skok sprawdzamy osobno: allowlista, anty-SSRF, pin IP. Skok bez tego byłby
  dziurą dokładnie w tym miejscu, w którym najbardziej boli.

**Instalacja ze źródeł nie jest aktualizowana.** Gdy Husarz nie działa jako binarka
(``sys.frozen``), nie ma czego podmieniać — komenda odmawia i mówi, czego użyć zamiast tego.
Milcząca próba podmiany interpretera byłaby znacznie gorsza niż odmowa.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

from husarz.config.schema import HusarzConfig
from husarz.ssrf import HostResolver, build_pinned_target, default_resolve

#: Limit rozmiaru pobieranej binarki. Wydania Husarza to dziesiątki megabajtów; setki
#: znaczyłyby, że pobieramy coś innego, niż sądzimy.
LIMIT_BINARKI = 400 * 1024 * 1024

#: Limit rozmiaru pliku podpisu — podpis Ed25519 to 64 bajty, base64 daje ~88.
_LIMIT_PODPISU = 4096

#: Ile przekierowań wolno wykonać. Serwer wydań robi jedno (na magazyn plików); dwa
#: zostawiają margines, a nieskończoność byłaby zaproszeniem do pętli i do obejścia pinu.
_MAKS_PRZEKIEROWAN = 2

#: Przyrostek pliku oczekującego na podmianę oraz kopii poprzedniej wersji.
PRZYROSTEK_NOWEJ = ".new"
PRZYROSTEK_STAREJ = ".old"


class BladInstalacji(Exception):
    """Aktualizacji nie da się pobrać, zweryfikować albo zainstalować."""


def nazwa_zasobu() -> str | None:
    """Nazwa artefaktu wydania dla TEGO systemu.

    Nazwy pochodzą z macierzy w ``.github/workflows/release.yml`` — muszą się zgadzać,
    inaczej aktualizator szukałby pliku, którego pipeline nie wytwarza.

    Returns:
        Nazwa pliku albo ``None``, gdy dla tego systemu nie budujemy wydań.
    """
    return {
        "win32": "husarz-app-windows.exe",
        "linux": "husarz-app-linux",
        "darwin": "husarz-app-macos",
    }.get(sys.platform)


def sciezka_binarki() -> Path | None:
    """Ścieżka uruchomionej binarki albo ``None`` przy instalacji ze źródeł.

    ``sys.frozen`` ustawia PyInstaller. Przy uruchomieniu przez ``python -m`` wskazywałby
    interpreter — podmiana czegoś takiego byłaby katastrofą, więc wolimy nie wiedzieć.

    Returns:
        Ścieżka binarki albo ``None``.
    """
    if not getattr(sys, "frozen", False):
        return None
    return Path(sys.executable).resolve()


def _dozwolony_host(url: str, dozwolone: list[str]) -> str:
    """Sprawdza host adresu wobec allowlisty. Zwraca pusty napis, gdy dozwolony."""
    host = (urlsplit(url).hostname or "").lower()
    if not host:
        return "adres pobrania nie zawiera hosta"
    if any(host == d or host.endswith(f".{d}") for d in dozwolone):
        return ""
    return (
        f"host '{host}' nie jest na liście `update.sources` — pobranie z tego adresu "
        f"nie zostało dozwolone"
    )


def pobierz(
    url: str,
    *,
    config: HusarzConfig,
    limit: int,
    resolve: HostResolver | None = None,
) -> bytes:
    """Pobiera zawartość adresu, sprawdzając KAŻDY skok przekierowania.

    Args:
        url: Adres startowy.
        config: Konfiguracja (allowlista ``update.sources``).
        limit: Górna granica rozmiaru w bajtach.
        resolve: Rozwiązywanie nazw — wstrzykiwalne w testach.

    Returns:
        Pobrane bajty.

    Raises:
        BladInstalacji: Gdy host jest niedozwolony, adres odrzucony przez anty-SSRF,
            odpowiedź jest błędna albo przekroczono limit rozmiaru bądź liczbę skoków.
    """
    import httpx  # noqa: PLC0415

    from husarz.core.errors import EgressError  # noqa: PLC0415

    rozwiaz: HostResolver = resolve if resolve is not None else default_resolve
    biezacy = url
    for _ in range(_MAKS_PRZEKIEROWAN + 1):
        powod = _dozwolony_host(biezacy, config.update.sources)
        if powod:
            raise BladInstalacji(powod)
        try:
            # Pin IP obowiązuje KAŻDY skok osobno (ADR-0020). Przekierowanie bez ponownej
            # walidacji byłoby drogą do dowolnego adresu — w tym do metadanych chmury.
            cel = build_pinned_target(
                biezacy, allow_loopback=False, allow_lan=False, resolve=rozwiaz
            )
        except EgressError as exc:
            raise BladInstalacji(f"adres odrzucony przez kontrolę anty-SSRF: {exc}") from exc

        naglowki = {}
        if cel.host_header is not None:
            naglowki["Host"] = cel.host_header
        rozszerzenia = {}
        if cel.sni_hostname is not None:
            rozszerzenia["sni_hostname"] = cel.sni_hostname
        try:
            with httpx.Client(
                timeout=httpx.Timeout(None, connect=15.0, read=120.0),
                follow_redirects=False,
                verify=True,
                trust_env=False,
            ) as klient:
                odp = klient.get(cel.connect_url, headers=naglowki, extensions=rozszerzenia)
                if odp.status_code in (301, 302, 303, 307, 308):
                    nastepny = odp.headers.get("location", "")
                    if not nastepny:
                        raise BladInstalacji("przekierowanie bez adresu docelowego")
                    biezacy = nastepny
                    continue
                if odp.status_code != 200:
                    raise BladInstalacji(f"dostawca odpowiedział kodem {odp.status_code}")
                dane = odp.content
        except BladInstalacji:
            raise
        except Exception as exc:  # noqa: BLE001 - treści nie logujemy (URL bywa w komunikacie)
            raise BladInstalacji("nie udało się pobrać pliku (sieć albo TLS)") from exc

        if len(dane) > limit:
            raise BladInstalacji(f"pobrany plik ma {len(dane)} B, a limit to {limit} B — przerwane")
        return dane
    raise BladInstalacji("zbyt wiele przekierowań")


def zweryfikuj_podpis(binarka: bytes, podpis_b64: str, material_klucza: str) -> None:
    """Sprawdza podpis Ed25519 nad bajtami binarki. Odmowa jest wyjątkiem, nie wartością.

    Args:
        binarka: Pobrane bajty.
        podpis_b64: Zawartość pliku ``.sig`` (base64 surowego podpisu).
        material_klucza: Klucz PUBLICZNY (PEM, OpenSSH albo base64 32 bajtów).

    Raises:
        BladInstalacji: Gdy podpisu nie da się odczytać albo się nie zgadza.
    """
    import base64  # noqa: PLC0415
    import binascii  # noqa: PLC0415

    from husarz.security.ed25519 import Ed25519Error, zweryfikuj  # noqa: PLC0415

    try:
        podpis = base64.b64decode(podpis_b64.strip(), validate=True)
    except (binascii.Error, ValueError) as exc:
        raise BladInstalacji("plik podpisu nie jest poprawnym base64") from exc
    if not podpis:
        raise BladInstalacji("plik podpisu jest pusty")
    try:
        poprawny = zweryfikuj(binarka, podpis, material_klucza)
    except Ed25519Error as exc:
        raise BladInstalacji(f"nie da się wczytać klucza weryfikującego: {exc}") from exc
    if not poprawny:
        raise BladInstalacji(
            "PODPIS SIĘ NIE ZGADZA — pobrany plik NIE pochodzi od posiadacza klucza "
            "podpisującego albo został po drodze zmieniony. Instalacja przerwana."
        )


def przygotuj(binarka: bytes, podpis_b64: str, cel: Path) -> Path:
    """Zapisuje zweryfikowaną binarkę jako plik oczekujący na podmianę.

    Zapis jest dwuetapowy: najpierw plik tymczasowy w TYM SAMYM katalogu (żeby podmiana
    była atomowa — ``replace`` działa w obrębie jednego systemu plików), potem podmiana
    pod nazwę ``<binarka>.new``. Obok ląduje podpis, bo przed samą podmianą weryfikujemy
    PONOWNIE: między pobraniem a restartem mija czas, a plik leży na dysku.

    Args:
        binarka: Zweryfikowane bajty.
        podpis_b64: Podpis do zapisania obok.
        cel: Ścieżka uruchomionej binarki.

    Returns:
        Ścieżka pliku oczekującego.

    Raises:
        BladInstalacji: Gdy zapis się nie powiedzie.
    """
    oczekujaca = cel.with_name(cel.name + PRZYROSTEK_NOWEJ)
    try:
        with tempfile.NamedTemporaryFile(dir=cel.parent, delete=False) as tmp:
            tmp.write(binarka)
            tmp.flush()
            os.fsync(tmp.fileno())
            tymczasowy = Path(tmp.name)
        os.chmod(tymczasowy, 0o700)
        tymczasowy.replace(oczekujaca)
        oczekujaca.with_name(oczekujaca.name + ".sig").write_text(podpis_b64, encoding="utf-8")
    except OSError as exc:
        raise BladInstalacji(f"nie można zapisać pliku aktualizacji: {exc}") from exc
    return oczekujaca


def zastosuj_oczekujaca(cel: Path, material_klucza: str) -> str:
    """Podmienia binarkę, jeśli czeka zweryfikowana nowa wersja. Wołane PRZY STARCIE.

    Kolejność na Windowsie jest wymuszona przez system: nie da się nadpisać działającego
    pliku ``.exe``, ale wolno go PRZEMIANOWAĆ. Robimy więc odsunięcie starej wersji, a potem
    wstawienie nowej — ta sama sekwencja działa też na POSIX-ie, więc mamy jedną ścieżkę
    zamiast dwóch.

    Args:
        cel: Ścieżka uruchomionej binarki.
        material_klucza: Klucz publiczny do PONOWNEJ weryfikacji.

    Returns:
        Komunikat dla operatora; pusty napis, gdy nie było czego stosować.

    Raises:
        BladInstalacji: Gdy plik oczekujący istnieje, ale nie przechodzi weryfikacji albo
            podmiana się nie powiedzie. Cicha rezygnacja byłaby gorsza: operator sądziłby,
            że działa na nowej wersji.
    """
    oczekujaca = cel.with_name(cel.name + PRZYROSTEK_NOWEJ)
    plik_podpisu = oczekujaca.with_name(oczekujaca.name + ".sig")
    if not oczekujaca.is_file():
        _sprzataj_stara(cel)
        return ""
    if not plik_podpisu.is_file():
        raise BladInstalacji(
            f"plik aktualizacji {oczekujaca.name} nie ma podpisu obok — nie instaluję"
        )
    try:
        zweryfikuj_podpis(
            oczekujaca.read_bytes(), plik_podpisu.read_text(encoding="utf-8"), material_klucza
        )
    except OSError as exc:
        raise BladInstalacji(f"nie można odczytać pliku aktualizacji: {exc}") from exc

    stara = cel.with_name(cel.name + PRZYROSTEK_STAREJ)
    try:
        cel.replace(stara)
        oczekujaca.replace(cel)
        os.chmod(cel, 0o700)
        plik_podpisu.unlink(missing_ok=True)
    except OSError as exc:
        raise BladInstalacji(f"nie udało się podmienić binarki: {exc}") from exc
    return f"Zainstalowano nową wersję Husarza (poprzednia: {stara.name})."


def _sprzataj_stara(cel: Path) -> None:
    """Usuwa kopię poprzedniej wersji, gdy nowa działa (czyli przy kolejnym starcie).

    Kasowanie od razu po podmianie byłoby przedwczesne: gdyby nowa binarka nie wstała,
    operator zostałby bez czego wracać. Skoro doszliśmy tutaj, nowa wersja się uruchomiła.
    """
    stara = cel.with_name(cel.name + PRZYROSTEK_STAREJ)
    if stara.is_file():
        try:
            stara.unlink()
        except OSError:  # pragma: no cover - rzadka ścieżka I/O
            return
