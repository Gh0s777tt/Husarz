"""Sprawdzanie dostępności nowszej wersji Husarza.

**Domyślnie wyłączone i to jest decyzja, nie ostrożność.** Samo zapytanie o wersję jest
połączeniem WYCHODZĄCYM: ujawnia serwerowi wydań, że ta instalacja istnieje, ma dany adres
IP i konkretną wersję. Projekt deklaruje zero telemetrii, więc mechanizm o takim skutku nie
może włączyć się sam.

**Trzy stany, nie dwa.** Tak samo jak w diagnozie (`husarz doctor`): „nie udało się
sprawdzić" NIGDY nie zaokrągla się do „masz aktualną wersję". Instalacja, która przez
tydzień nie mogła dobić do serwera wydań, ma o tym powiedzieć — inaczej cisza znaczyłaby
dwie zupełnie różne rzeczy naraz.

**Dwie allowlisty, nie jedna.** Zapytanie idzie przez ``update.sources``, a nie przez
``security.egress.allowlist``. Zgoda na pytanie o wersję nie może po cichu otwierać tej
domeny narzędziu ``web``, wtyczkom MCP ani agentom.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, runtime_checkable

from husarz.config.schema import HusarzConfig
from husarz.ssrf import HostResolver, build_pinned_target, default_resolve

#: Limit czasu zapytania o wersję. Krótki celowo: sprawdzenie aktualizacji nie może
#: opóźniać startu platformy — brak odpowiedzi ma dać stan NIEZNANY, a nie zawieszenie.
_TIMEOUT_SEKUND = 5.0

#: Górna granica odpowiedzi serwera wydań. Metadane wydania to kilka kilobajtów; większa
#: odpowiedź znaczy, że rozmawiamy z czymś innym, niż sądzimy.
_LIMIT_ODPOWIEDZI = 512 * 1024

#: Numer wersji semantycznej, z opcjonalnym „v" z przodu (tak taguje wydania ten projekt).
_WZORZEC_WERSJI = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")


class Stan(StrEnum):
    """Wynik sprawdzenia — trzy stany, bo „nie wiem" to osobna odpowiedź."""

    #: Wersja bieżąca jest najnowsza znana.
    AKTUALNA = "aktualna"
    #: Jest nowsze wydanie.
    DOSTEPNA = "dostepna"
    #: Nie dało się sprawdzić. NIGDY nie zaokrąglamy tego do ``AKTUALNA``.
    NIEZNANY = "nieznany"
    #: Mechanizm wyłączony w konfiguracji — nie próbowaliśmy.
    WYLACZONE = "wylaczone"


@dataclass(frozen=True, slots=True)
class Zasob:
    """Plik dołączony do wydania.

    Attributes:
        nazwa: Nazwa pliku u dostawcy (np. ``husarz-app-linux``).
        url: Adres pobrania.
    """

    nazwa: str
    url: str


@dataclass(frozen=True, slots=True)
class Wydanie:
    """Wydanie znalezione u dostawcy.

    Attributes:
        wersja: Numer wersji tak, jak podał go serwer (np. ``v0.15.0``).
        strona: Adres strony wydania — do POKAZANIA operatorowi, nie do pobierania.
        zasoby: Pliki dołączone do wydania (binarki i ich podpisy).
    """

    wersja: str
    strona: str
    zasoby: tuple[Zasob, ...] = ()

    def zasob(self, nazwa: str) -> Zasob | None:
        """Zwraca zasób o dokładnie tej nazwie albo ``None``.

        Dopasowanie jest ŚCISŁE, nie „zawiera": nazwa artefaktu decyduje o tym, co zostanie
        uruchomione na maszynie operatora, więc dopasowanie przybliżone byłoby zaproszeniem
        do podstawienia innego pliku.

        Args:
            nazwa: Oczekiwana nazwa pliku.

        Returns:
            Zasób albo ``None``.
        """
        return next((z for z in self.zasoby if z.nazwa == nazwa), None)


@runtime_checkable
class ZrodloWydan(Protocol):
    """Skąd bierzemy informację o najnowszym wydaniu.

    Szew wstrzykiwalny: testy nie wykonują połączeń sieciowych, a produkcja używa
    :class:`ZrodloGitHub`.
    """

    def najnowsze(self) -> tuple[Wydanie | None, str]:
        """Zwraca najnowsze wydanie albo ``(None, powód)``, gdy nie dało się ustalić."""
        ...


@dataclass(frozen=True, slots=True)
class WynikSprawdzenia:
    """Odpowiedź na pytanie „czy jest nowsza wersja".

    Attributes:
        stan: Rozstrzygnięcie w trzech stanach (plus ``WYLACZONE``).
        biezaca: Wersja tej instalacji.
        najnowsza: Wersja u dostawcy (pusta, gdy nieznana).
        strona: Adres strony wydania (pusty, gdy nieznany).
        powod: Czytelne wyjaśnienie — WYMAGANE dla stanu ``NIEZNANY``, bo bez niego
            operator nie wie, czy to awaria sieci, limit dostawcy, czy błąd konfiguracji.
    """

    stan: Stan
    biezaca: str
    najnowsza: str = ""
    strona: str = ""
    powod: str = ""


def rozbierz_wersje(tekst: str) -> tuple[int, int, int] | None:
    """Rozbiera numer wersji semantycznej. ``None`` = nie da się porównać.

    Świadomie NIE obsługujemy wersji przedpremierowych (``1.0.0-rc1``) ani metadanych
    budowania. Nie jest to niedopatrzenie: porównywanie ich wymaga pełnej reguły SemVer,
    a projekt taguje wydania trójkami. Wersja o nieznanym kształcie daje ``None``, czyli
    stan NIEZNANY — zgadywanie porządku byłoby gorsze niż przyznanie się do niewiedzy.

    Args:
        tekst: Numer wersji, opcjonalnie z przedrostkiem ``v``.

    Returns:
        Trójka liczb albo ``None``.
    """
    dopasowanie = _WZORZEC_WERSJI.match(tekst.strip())
    if dopasowanie is None:
        return None
    return (int(dopasowanie[1]), int(dopasowanie[2]), int(dopasowanie[3]))


def sprawdz(
    config: HusarzConfig, biezaca: str, *, zrodlo: ZrodloWydan | None = None
) -> WynikSprawdzenia:
    """Ustala, czy dostępna jest nowsza wersja Husarza.

    Args:
        config: Wczytana konfiguracja (sekcja ``update``).
        biezaca: Wersja tej instalacji (``husarz.__version__``).
        zrodlo: Źródło wydań; ``None`` = zbuduj domyślne (GitHub) z konfiguracji.

    Returns:
        Wynik w trzech stanach. Nigdy nie rzuca — awaria sieci to stan ``NIEZNANY``,
        a nie wyjątek przerywający start platformy.
    """
    if not config.update.enabled:
        return WynikSprawdzenia(
            stan=Stan.WYLACZONE,
            biezaca=biezaca,
            powod="sprawdzanie aktualizacji jest wyłączone (`update.enabled`)",
        )

    moja = rozbierz_wersje(biezaca)
    if moja is None:
        return WynikSprawdzenia(
            stan=Stan.NIEZNANY,
            biezaca=biezaca,
            powod=f"nie umiem odczytać własnej wersji '{biezaca}'",
        )

    uzyte = zrodlo if zrodlo is not None else ZrodloGitHub(config)
    wydanie, powod = uzyte.najnowsze()
    if wydanie is None:
        return WynikSprawdzenia(stan=Stan.NIEZNANY, biezaca=biezaca, powod=powod)

    ich = rozbierz_wersje(wydanie.wersja)
    if ich is None:
        return WynikSprawdzenia(
            stan=Stan.NIEZNANY,
            biezaca=biezaca,
            najnowsza=wydanie.wersja,
            strona=wydanie.strona,
            powod=f"nie umiem porównać wersji '{wydanie.wersja}' z '{biezaca}'",
        )

    return WynikSprawdzenia(
        stan=Stan.DOSTEPNA if ich > moja else Stan.AKTUALNA,
        biezaca=biezaca,
        najnowsza=wydanie.wersja,
        strona=wydanie.strona,
    )


class ZrodloGitHub:
    """Odczyt najnowszego wydania z API GitHuba.

    Przechodzi przez te same zabezpieczenia, co każda inna droga wychodząca: allowlistę
    ``update.sources`` ORAZ pin IP (ADR-0020). Nazwa serwera wydań nie może rozwiązać się
    na adres metadanych chmury ani na zakres wewnętrzny operatora.

    Args:
        config: Konfiguracja (repozytorium i allowlista).
        resolve: Rozwiązywanie nazw — wstrzykiwalne dla testów.
    """

    def __init__(self, config: HusarzConfig, *, resolve: HostResolver | None = None) -> None:
        self._config = config
        self._resolve: HostResolver = resolve if resolve is not None else default_resolve

    def najnowsze(self) -> tuple[Wydanie | None, str]:
        """Pyta API o najnowsze wydanie repozytorium.

        Returns:
            Para ``(wydanie, powód)``. Przy niepowodzeniu wydanie jest ``None``, a powód
            opisuje, CO zawiodło — nigdy nie zwracamy cichego braku.
        """
        import httpx  # noqa: PLC0415

        from husarz.core.errors import EgressError  # noqa: PLC0415

        repo = self._config.update.repository
        if repo is None:  # pragma: no cover - walidacja nie dopuszcza włączonego bez repo
            return None, "brak `update.repository`"
        url = f"https://api.github.com/repos/{repo}/releases/latest"

        host = "api.github.com"
        dozwolone = self._config.update.sources
        # Dopasowanie dokładne albo po domenie nadrzędnej — jak w bramce egress.
        if not any(host == d or host.endswith(f".{d}") for d in dozwolone):
            return None, (
                f"host '{host}' nie jest na liście `update.sources` — zgoda na pytanie "
                f"o wersję nie została udzielona"
            )
        try:
            cel = build_pinned_target(
                url, allow_loopback=False, allow_lan=False, resolve=self._resolve
            )
        except EgressError as exc:
            return None, f"adres serwera wydań odrzucony przez kontrolę anty-SSRF: {exc}"

        naglowki = {"Accept": "application/vnd.github+json"}
        if cel.host_header is not None:
            naglowki["Host"] = cel.host_header
        rozszerzenia = {}
        if cel.sni_hostname is not None:
            rozszerzenia["sni_hostname"] = cel.sni_hostname
        try:
            with httpx.Client(
                timeout=_TIMEOUT_SEKUND,
                follow_redirects=False,
                verify=True,
                trust_env=False,
            ) as klient:
                odp = klient.get(cel.connect_url, headers=naglowki, extensions=rozszerzenia)
            if odp.status_code == 404:
                return None, f"repozytorium '{repo}' nie ma wydań albo nie istnieje"
            if odp.status_code == 403:
                return None, "serwer wydań odrzucił zapytanie (prawdopodobnie limit tempa)"
            if odp.status_code != 200:
                return None, f"serwer wydań odpowiedział kodem {odp.status_code}"
            if len(odp.content) > _LIMIT_ODPOWIEDZI:
                return None, "odpowiedź serwera wydań jest podejrzanie duża jak na metadane"
            dane = odp.json()
        except Exception:  # noqa: BLE001 - treści nie logujemy: URL bywa w komunikacie
            return None, "nie udało się połączyć z serwerem wydań (sieć albo format odpowiedzi)"

        if not isinstance(dane, dict):
            return None, "serwer wydań zwrócił nieoczekiwany format"
        tag = dane.get("tag_name")
        if not isinstance(tag, str) or not tag:
            return None, "odpowiedź serwera wydań nie zawiera numeru wersji"
        strona = dane.get("html_url")
        zasoby: list[Zasob] = []
        for pozycja in dane.get("assets") or []:
            if not isinstance(pozycja, dict):
                continue
            nazwa = pozycja.get("name")
            adres = pozycja.get("browser_download_url")
            if isinstance(nazwa, str) and isinstance(adres, str) and nazwa and adres:
                zasoby.append(Zasob(nazwa=nazwa, url=adres))
        return (
            Wydanie(
                wersja=tag,
                strona=strona if isinstance(strona, str) else "",
                zasoby=tuple(zasoby),
            ),
            "",
        )
