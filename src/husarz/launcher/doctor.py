"""Diagnoza instalacji — JEDNO źródło prawdy dla CLI i konsoli WWW.

**Po co ten moduł istnieje.** Operator pobiera gotową binarkę launchera, uruchamia ją, konsola
wstaje — i czat odpowiada gołym ``502 Backend modelu zawiódł``. W logu startowym nie ma nic.
Odtworzone: brak jakiejkolwiek podpowiedzi, co jest nie tak ani co z tym zrobić. Ten moduł
zamienia tę ciszę w listę konkretnych ustaleń z instrukcją naprawy.

**Trzy stany, nie dwa.** Kontrola kończy się jako ``OK``, ``PROBLEM`` albo **NIEZNANY**.
Ostatni jest osobny celowo: projekt ma twardą zasadę, że pomiar NIE MOŻE zaokrąglać
„nie dało się sprawdzić" do „w porządku". Diagnoza, która przy braku Dockera mówi
„sandbox OK", jest gorsza niż brak diagnozy, bo operator przestaje szukać.

**Sondy są wstrzykiwane.** Wszystko, co dotyka sieci albo systemu plików, przechodzi przez
:class:`Sonda`. Dzięki temu cały moduł jest testowalny offline — tak jak executor sandboxa,
fetcher narzędzia ``web`` i resolver DNS w pozostałych warstwach projektu.

**Diagnoza NIE jest obejściem bramki egress.** Sondowanie endpointu to połączenie wychodzące,
więc przechodzi przez tę samą kontrolę, co ruch routera. Endpoint spoza allowlisty nie jest
odpytywany — kontrola kończy się stanem NIEZNANY z podaniem powodu. Bez tego ``doctor`` byłby
skanerem portów uruchamianym przez API.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Protocol, runtime_checkable

from husarz.config.schema import HusarzConfig
from husarz.launcher.diagnostics import port_conflicts


class Waga(StrEnum):
    """Jak bardzo ustalenie przeszkadza w działaniu."""

    BLOKUJACA = "blokujaca"
    """Bez naprawy funkcja NIE zadziała (np. brak modelu czatu u dostawcy)."""

    OSTRZEZENIE = "ostrzezenie"
    """Zadziała, ale prawdopodobnie nie tak, jak operator oczekuje."""

    INFORMACJA = "informacja"
    """Stan wart pokazania, nie wymagający działania."""


class Stan(StrEnum):
    """Wynik pojedynczej kontroli."""

    OK = "ok"
    PROBLEM = "problem"
    NIEZNANY = "nieznany"
    """Nie dało się sprawdzić. NIGDY nie zaokrąglamy tego do ``OK``."""


@dataclass(frozen=True, slots=True)
class Ustalenie:
    """Wynik jednej kontroli diagnostycznej.

    Attributes:
        id: Krótki, stabilny identyfikator (do odwołań w dokumentacji i UI).
        stan: OK / PROBLEM / NIEZNANY.
        waga: Istotność — brana pod uwagę tylko dla stanu innego niż OK.
        opis: Co ustalono, jednym zdaniem, po polsku.
        naprawa: Co operator ma zrobić. Pusty dla stanu OK.
    """

    id: str
    stan: Stan
    waga: Waga
    opis: str
    naprawa: str = ""


@runtime_checkable
class Sonda(Protocol):
    """Dostęp do świata zewnętrznego — wstrzykiwany, żeby diagnoza była testowalna offline."""

    def modele_u_dostawcy(self, endpoint: str) -> list[str] | None:
        """Zwraca nazwy modeli dostępne pod endpointem albo ``None``, gdy nie dało się ustalić.

        ``None`` znaczy „nie wiem" (brak odpowiedzi, timeout, nieznany format) i przekłada się
        na stan NIEZNANY — nigdy na pustą listę, bo pusta lista znaczy „dostawca odpowiedział
        i nie ma żadnego modelu", co jest zupełnie innym ustaleniem.
        """
        ...

    def czy_zapisywalny(self, katalog: Path) -> bool | None:
        """Czy da się pisać do katalogu; ``None``, gdy nie dało się ustalić."""
        ...

    def powod_odmowy_sondowania(self, endpoint: str) -> str | None:
        """Dlaczego NIE WOLNO sondować endpointu; ``None``, gdy wolno.

        Osobne od :meth:`modele_u_dostawcy`, bo „nie wolno nam zapytać" i „silnik nie
        odpowiedział" to DWA różne ustalenia, a operator dostaje inną instrukcję naprawy.
        Bez tego rozróżnienia diagnoza kłamała o przyczynie: przy zablokowanym egressie
        mówiła, że silnik milczy, choć nikt go nie pytał.
        """
        ...


def _kanoniczna(nazwa: str) -> str:
    """Sprowadza nazwę modelu do postaci porównywalnej: brak etykiety znaczy ``:latest``.

    Ollama tak właśnie interpretuje nazwę bez etykiety, więc ``husarz`` i ``husarz:latest``
    to ten sam model.
    """
    return nazwa if ":" in nazwa else f"{nazwa}:latest"


def _pasuje(oczekiwany: str, dostepne: Sequence[str]) -> bool:
    """Czy model z konfiguracji odpowiada któremuś z modeli u dostawcy.

    Porównanie musi znosić BRAK etykiety, ale **nie wolno mu znosić etykiety RÓŻNEJ**.
    Pierwsza wersja obcinała etykietę po obu stronach i przez to zrównywała
    ``qwen2.5-coder:7b`` z ``qwen2.5-coder:1.5b`` — czyli meldowała „model jest", gdy był
    zupełnie inny wariant. Fałszywe OK jest w diagnozie gorsze niż fałszywy alarm: operator
    przestaje szukać. Wykryte przy przeglądzie przestrzeni awarii, potwierdzone uruchomieniem.

    Kanonizujemy więc obie strony (brak etykiety → ``:latest``) i porównujemy dokładnie.

    Args:
        oczekiwany: Nazwa modelu z konfiguracji.
        dostepne: Nazwy zgłoszone przez dostawcę.

    Returns:
        Czy któraś z nazw oznacza DOKŁADNIE ten sam model.
    """
    cel = _kanoniczna(oczekiwany)
    return any(_kanoniczna(n) == cel for n in dostepne)


def _kontrola_modelu_czatu(config: HusarzConfig, sonda: Sonda) -> list[Ustalenie]:
    """Czy model trybu czatu istnieje w rejestrze i czy dostawca faktycznie go ma.

    To jest kontrola, dla której ten moduł powstał: bez niej operator dostaje ``502`` i tyle.
    """
    # `models.chat` bywa pusty — wtedy tryb czatu spada na model DOMYŚLNY. Odtwarzamy
    # tę samą regułę co `_resolve_chat_model` w API, żeby diagnoza opisywała model,
    # który faktycznie obsłuży żądanie, a nie ten wpisany w konfiguracji.
    nazwa = config.models.chat or config.models.default
    spec = config.models.registry.get(nazwa)
    if spec is None:
        # Walidacja schematu tego pilnuje (`models.chat` musi istnieć w rejestrze), więc
        # przez `load_config` tu nie wejdziemy. Gałąź zostaje, bo `zdiagnozuj` przyjmuje
        # DOWOLNY obiekt konfiguracji — także zbudowany programowo — a diagnoza, która sama
        # wywala się na `None`, byłaby bezużyteczna dokładnie wtedy, gdy jest potrzebna.
        return [
            Ustalenie(
                id="model-czatu-w-rejestrze",
                stan=Stan.PROBLEM,
                waga=Waga.BLOKUJACA,
                opis=f"Model trybu czatu '{nazwa}' nie istnieje w rejestrze modeli.",
                naprawa=(
                    f"Dodaj '{nazwa}' do `models.registry` albo zmień `models.chat` na jeden "
                    f"z: {', '.join(sorted(config.models.registry))}."
                ),
            )
        ]

    ustalenia: list[Ustalenie] = []

    # Luka, której schemat NIE pilnuje: `models.chat` może wskazywać model WYŁĄCZONY.
    # Konfiguracja wczytuje się bez zastrzeżeń, a czat wywraca się dopiero przy pierwszym
    # żądaniu — sprawdzone.
    if not spec.enabled:
        ustalenia.append(
            Ustalenie(
                id="model-czatu-wlaczony",
                stan=Stan.PROBLEM,
                waga=Waga.BLOKUJACA,
                opis=f"Model trybu czatu '{nazwa}' jest WYŁĄCZONY (`enabled: false`).",
                naprawa=(
                    f"Ustaw `enabled: true` dla '{nazwa}' w config/models.yaml albo wskaż "
                    f"w `models.chat` inny, włączony model."
                ),
            )
        )

    # Druga luka schematu: brak endpointu przy backendzie, który go potrzebuje.
    if spec.endpoint is None:
        ustalenia.append(
            Ustalenie(
                id="model-czatu-u-dostawcy",
                stan=Stan.PROBLEM,
                waga=Waga.BLOKUJACA,
                opis=(
                    f"Model '{nazwa}' (backend: {spec.backend}) nie ma endpointu — "
                    f"nie ma dokąd wysłać żądania."
                ),
                naprawa=(
                    f"Ustaw `endpoint` dla '{nazwa}' w config/models.yaml "
                    f"(np. http://localhost:11434/v1 dla Ollamy)."
                ),
            )
        )
        return ustalenia

    odmowa = sonda.powod_odmowy_sondowania(spec.endpoint)
    if odmowa is not None:
        ustalenia.append(
            Ustalenie(
                id="model-czatu-u-dostawcy",
                stan=Stan.NIEZNANY,
                waga=Waga.BLOKUJACA,
                opis=(
                    f"Nie sprawdzono modelu '{spec.model}': polityka egress nie pozwala "
                    f"odpytać {spec.endpoint} ({odmowa})."
                ),
                naprawa=(
                    "Router ODMÓWI temu modelowi z tego samego powodu. Dodaj host do "
                    "`security.egress.allowlist` albo wskaż endpoint lokalny. "
                    "W profilu airgap zdalny endpoint jest zabroniony z założenia."
                ),
            )
        )
        return ustalenia

    dostepne = sonda.modele_u_dostawcy(spec.endpoint)
    if dostepne is None:
        ustalenia.append(
            Ustalenie(
                id="model-czatu-u-dostawcy",
                stan=Stan.NIEZNANY,
                waga=Waga.BLOKUJACA,
                opis=(
                    f"Silnik modelu pod {spec.endpoint} nie odpowiedział — nie wiadomo, "
                    f"czy model '{spec.model}' jest dostępny."
                ),
                naprawa=(
                    "Uruchom silnik (np. `ollama serve`) i sprawdź, czy endpoint w "
                    "config/models.yaml wskazuje właściwy port. Instrukcja: ollama/README.md."
                ),
            )
        )
        return ustalenia

    if _pasuje(spec.model, dostepne):
        ustalenia.append(
            Ustalenie(
                id="model-czatu-u-dostawcy",
                stan=Stan.OK,
                waga=Waga.INFORMACJA,
                opis=f"Silnik pod {spec.endpoint} ma model '{spec.model}'.",
            )
        )
        return ustalenia

    ustalenia.append(
        Ustalenie(
            id="model-czatu-u-dostawcy",
            stan=Stan.PROBLEM,
            waga=Waga.BLOKUJACA,
            opis=(
                f"Silnik odpowiada, ale NIE MA modelu '{spec.model}'. "
                f"Dostępne: {', '.join(sorted(dostepne)) or '(żadnego)'}."
            ),
            naprawa=(
                "Przygotuj model wg ollama/README.md (`ollama create ...`) albo zmień pole "
                "`model` w config/models.yaml na jeden z dostępnych."
            ),
        )
    )
    return ustalenia


def _kontrola_katalogow(config: HusarzConfig, sonda: Sonda) -> list[Ustalenie]:
    """Czy katalogi, do których Husarz pisze, są zapisywalne.

    Niezapisywalny katalog audytu jest szczególnie groźny: audyt jest twardym wymogiem
    i fail-closed, więc akcja nie „udaje się" bez zapisu — ale operator dowiaduje się o tym
    dopiero przy pierwszej akcji, jako 503.
    """
    katalogi = {
        "dane": config.platform.data_dir,
        "artefakty": config.platform.artifacts_dir,
        "przestrzeń robocza": config.platform.workspace_dir,
    }
    ustalenia: list[Ustalenie] = []
    for opis_katalogu, sciezka in katalogi.items():
        wynik = sonda.czy_zapisywalny(sciezka)
        if wynik is True:
            continue
        if wynik is None:
            ustalenia.append(
                Ustalenie(
                    id=f"katalog-{opis_katalogu}",
                    stan=Stan.NIEZNANY,
                    waga=Waga.OSTRZEZENIE,
                    opis=f"Nie dało się sprawdzić, czy katalog {opis_katalogu} jest zapisywalny.",
                    naprawa=f"Sprawdź ręcznie uprawnienia do {sciezka}.",
                )
            )
            continue
        ustalenia.append(
            Ustalenie(
                id=f"katalog-{opis_katalogu}",
                stan=Stan.PROBLEM,
                waga=Waga.BLOKUJACA,
                opis=f"Katalog {opis_katalogu} nie jest zapisywalny.",
                naprawa=(
                    f"Nadaj prawa zapisu do {sciezka} albo wskaż inny katalog w konfiguracji "
                    f"(`platform.data_dir` i pokrewne). Uruchomienie binarki dwuklikiem "
                    f"startuje w katalogu domowym — ścieżki względne liczą się od niego."
                ),
            )
        )
    return ustalenia


def _kontrola_portow(config: HusarzConfig, host: str, port: int) -> list[Ustalenie]:
    """Czy któryś model nie celuje w port, na którym stanie sam Husarz."""
    kolizje = port_conflicts(config, host=host, port=port)
    if not kolizje:
        return []
    modele = ", ".join(f"{k.model_id} ({k.endpoint})" for k in kolizje)
    return [
        Ustalenie(
            id="kolizja-portu",
            stan=Stan.PROBLEM,
            waga=Waga.OSTRZEZENIE,
            opis=f"Endpoint modelu celuje w port {port}, na którym nasłuchuje Husarz: {modele}.",
            naprawa=(
                "Uruchom Husarza na innym porcie (`--port`) albo popraw endpoint modelu. "
                "Bez tego żądanie do modelu wraca do własnego API."
            ),
        )
    ]


def zdiagnozuj(
    config: HusarzConfig, *, sonda: Sonda, host: str = "127.0.0.1", port: int = 8000
) -> list[Ustalenie]:
    """Uruchamia komplet kontroli i zwraca ustalenia posortowane wg wagi.

    Args:
        config: Wczytana konfiguracja.
        sonda: Dostęp do świata zewnętrznego (sieć, system plików).
        host: Adres, na którym stanie (albo stoi) Husarz — do wykrycia kolizji portu.
        port: Port jw.

    Returns:
        Ustalenia: najpierw problemy blokujące, na końcu informacje. Lista NIGDY nie jest
        pusta — brak problemów też jest ustaleniem, które operator ma zobaczyć.
    """
    ustalenia: list[Ustalenie] = []
    ustalenia += _kontrola_modelu_czatu(config, sonda)
    ustalenia += _kontrola_katalogow(config, sonda)
    ustalenia += _kontrola_portow(config, host, port)

    kolejnosc = {Stan.PROBLEM: 0, Stan.NIEZNANY: 1, Stan.OK: 2}
    waga_kolejnosc = {Waga.BLOKUJACA: 0, Waga.OSTRZEZENIE: 1, Waga.INFORMACJA: 2}
    return sorted(ustalenia, key=lambda u: (kolejnosc[u.stan], waga_kolejnosc[u.waga], u.id))


def sformatuj(ustalenia: Sequence[Ustalenie]) -> list[str]:
    """Zamienia ustalenia na linie do wypisania w terminalu.

    Args:
        ustalenia: Wynik :func:`zdiagnozuj`.

    Returns:
        Linie gotowe do wypisania (bez znaku nowej linii).
    """
    znaki = {Stan.OK: "[ok]", Stan.PROBLEM: "[!!]", Stan.NIEZNANY: "[??]"}
    linie: list[str] = []
    for u in ustalenia:
        linie.append(f"{znaki[u.stan]} {u.id}: {u.opis}")
        if u.naprawa:
            linie.append(f"     → {u.naprawa}")
    # Podsumowanie MUSI zgadzać się z listą powyżej. Pierwsza wersja liczyła wyłącznie
    # problemy blokujące i stany nieznane, więc przy problemie NIEBLOKUJĄCYM kończyła się
    # zdaniem „Wszystkie kontrole przeszły" — mając wypisany problem dwie linie wyżej.
    # Wyszło na pierwszym uruchomieniu narzędzia.
    blokujace = [u for u in ustalenia if u.stan is Stan.PROBLEM and u.waga is Waga.BLOKUJACA]
    inne_problemy = [
        u for u in ustalenia if u.stan is Stan.PROBLEM and u.waga is not Waga.BLOKUJACA
    ]
    nieznane = [u for u in ustalenia if u.stan is Stan.NIEZNANY]

    czesci: list[str] = []
    if blokujace:
        czesci.append(f"problemów blokujących: {len(blokujace)}")
    if inne_problemy:
        czesci.append(f"ostrzeżeń: {len(inne_problemy)}")
    if nieznane:
        czesci.append(f'kontroli NIE DAŁO SIĘ wykonać: {len(nieznane)} (to nie to samo co „OK")')

    if not czesci:
        linie.append("\nWszystkie kontrole przeszły.")
    else:
        linie.append("\nPodsumowanie — " + "; ".join(czesci) + ".")
    return linie


class SondaSystemowa:
    """Realna sonda: pyta silnik modelu i sprawdza katalogi na tej maszynie.

    **Bramka egress obowiązuje też tutaj.** Sondowanie endpointu to połączenie wychodzące.
    Endpoint spoza allowlisty NIE jest odpytywany — zwracamy ``None`` (stan NIEZNANY), a nie
    wynik. Bez tego ``doctor`` wystawiony w konsoli byłby skanerem portów: wystarczyłoby
    wpisać dowolny adres jako endpoint modelu i odczytać z diagnozy, czy odpowiada.

    Args:
        config: Konfiguracja — potrzebna do polityki egress.
        timeout: Sekundy na odpowiedź silnika. Krótki celowo: diagnoza ma być szybka,
            a brak odpowiedzi w kilka sekund i tak znaczy „coś jest nie tak".
    """

    def __init__(self, config: HusarzConfig, *, timeout: float = 3.0) -> None:
        self._config = config
        self._timeout = timeout

    def powod_odmowy_sondowania(self, endpoint: str) -> str | None:
        """Sprawdza politykę egress TĄ SAMĄ funkcją, której użyje router."""
        from husarz.router.egress import EgressError, check_endpoint_allowed  # noqa: PLC0415

        try:
            check_endpoint_allowed(endpoint, self._config.security.egress)
        except EgressError as exc:
            return str(exc)
        return None

    def modele_u_dostawcy(self, endpoint: str) -> list[str] | None:
        """Pyta silnik o listę modeli. ``None``, gdy nie dało się ustalić — patrz Protocol.

        Bramkę egress sprawdzamy PONOWNIE, mimo że wołający pyta o nią osobno: ta metoda
        jest publiczna, a wyjście do sieci nie może zależeć od tego, czy ktoś pamiętał
        zapytać wcześniej.
        """
        if self.powod_odmowy_sondowania(endpoint) is not None:
            return None
        return self._zapytaj(endpoint)

    def _zapytaj(self, endpoint: str) -> list[str] | None:
        """Odpytuje endpoint dwoma wariantami: OpenAI-compat i natywnym Ollamy."""
        import httpx  # noqa: PLC0415

        baza = endpoint.rstrip("/").removesuffix("/v1")
        proby = (
            (f"{endpoint.rstrip('/')}/models", _nazwy_openai),
            (f"{baza}/api/tags", _nazwy_ollama),
        )
        for url, wydobadz in proby:
            try:
                # `trust_env=False` jak w pozostałych transportach: zmienne PROXY nie mogą
                # przekierować sondy przez cudzy serwer.
                with httpx.Client(timeout=self._timeout, trust_env=False) as klient:
                    odp = klient.get(url)
                if odp.status_code != 200:
                    continue
                nazwy = wydobadz(odp.json())
            # noqa S112: świadomie NIE logujemy — treść wyjątku transportowego potrafi
            # nieść URL z parametrami, a diagnoza ma trafiać także do zgłoszeń błędów.
            # Każda awaria znaczy tu „nie wiem", nigdy „brak modeli" — patrz Protocol.
            except Exception:  # noqa: BLE001, S112 - „nie wiem", bez logowania treści
                continue
            if nazwy is not None:
                return nazwy
        return None

    def czy_zapisywalny(self, katalog: Path) -> bool | None:
        """Próbuje utworzyć i skasować plik próbny. ``None``, gdy nie dało się rozstrzygnąć."""
        import tempfile  # noqa: PLC0415

        try:
            katalog.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=katalog, prefix=".husarz-proba-"):
                return True
        except OSError:
            return False
        except Exception:  # noqa: BLE001 - nieoczekiwane = „nie wiem", nie „nie da się"
            return None


def _nazwy_openai(dane: object) -> list[str] | None:
    """Wydobywa nazwy modeli z odpowiedzi OpenAI-compat (``{"data": [{"id": ...}]}``)."""
    if not isinstance(dane, dict):
        return None
    pozycje = dane.get("data")
    if not isinstance(pozycje, list):
        return None
    return [str(p["id"]) for p in pozycje if isinstance(p, dict) and "id" in p]


def _nazwy_ollama(dane: object) -> list[str] | None:
    """Wydobywa nazwy z natywnej odpowiedzi Ollamy (``{"models": [{"name": "x:latest"}]}``).

    Zwraca nazwy DOKŁADNIE tak, jak podaje je dostawca (z etykietą). Zniesienie etykiety
    należy do :func:`_pasuje` — jednego miejsca dla obu wariantów endpointu.
    """
    if not isinstance(dane, dict):
        return None
    pozycje = dane.get("models")
    if not isinstance(pozycje, list):
        return None
    return [str(p["name"]) for p in pozycje if isinstance(p, dict) and "name" in p]
