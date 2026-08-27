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

**Dwa poziomy głębokości, drugi jest OPT-IN.** Kontrola podstawowa pyta silnik, czy WYMIENIA
model w katalogu — to deklaracja. Kontrola głęboka (:class:`SondaGleboka`, `husarz doctor
--probe`) wysyła do modelu prawdziwe żądanie uzupełnienia i jest jedynym pomiarem SKUTKU
w całym module. Jest opcjonalna, bo ma skutki uboczne: wczytuje wagi do pamięci i potrafi
trwać minuty. Opt-in jest strukturalny — bez przekazanego obiektu sondy nie ma czym zapytać.
Ta droga NIE jest wystawiona przez API (patrz ADR-0024): wczytywanie wag na żądanie HTTP
byłoby dźwignią do wyczerpania zasobów.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Protocol, runtime_checkable

from husarz.config.schema import HusarzConfig, ModelBackend, ModelSpec
from husarz.config.secrets import SecretsProvider
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


class PowodOdmowy(StrEnum):
    """Dlaczego model NIE odpowiedział — kategoria, nie surowa treść wyjątku.

    Kategoria istnieje, bo każdy z tych przypadków wymaga INNEJ naprawy, a komunikat
    transportu jest celowo generyczny („Błąd HTTP przy wywołaniu modelu"): trafia do
    odpowiedzi API i do audytu, więc nie może nieść URL-a ani wnętrzności biblioteki.
    Diagnoza sięga po przyczynę głębiej, ale **nigdy nie przepisuje jej treści do wyjścia** —
    zamienia ją na jedną z poniższych kategorii i własny opis. Dzięki temu operator dostaje
    właściwą instrukcję, a komunikat nie zaczyna echem odsyłać tego, co dostał na wejściu.
    """

    TIMEOUT = "timeout"
    """Silnik nie odpowiedział w czasie. NIE jest to dowód awarii — model bywa ładowany."""

    UWIERZYTELNIENIE = "uwierzytelnienie"
    """401/403 — silnik odrzucił klucz API (albo go wymaga, a nie ma `api_key_ref`)."""

    NIE_ZNALEZIONO = "nie-znaleziono"
    """404 — endpoint nie obsługuje `/chat/completions` albo nie zna tego modelu."""

    BLAD_SILNIKA = "blad-silnika"
    """5xx — silnik przyjął żądanie i przewrócił się na nim (najczęściej brak pamięci)."""

    POLACZENIE = "polaczenie"
    """Nie udało się nawiązać połączenia (silnik przestał odpowiadać między kontrolami)."""

    ZLA_ODPOWIEDZ = "zla-odpowiedz"
    """Odpowiedź przyszła, ale nie w formacie OpenAI — endpoint udaje coś, czym nie jest."""

    BRAK_SEKRETU = "brak-sekretu"
    """Model deklaruje `api_key_ref`, ale sekretu nie da się rozwiązać. NIC nie wysłano."""

    EGRESS = "egress"
    """Polityka egress nie pozwoliła wysłać żądania. NIC nie wysłano."""

    BRAK_ENDPOINTU = "brak-endpointu"
    """Model nie ma endpointu — nie ma dokąd wysłać. NIC nie wysłano."""

    ROZWIAZANIE_NAZWY = "rozwiazanie-nazwy"
    """Nazwa się nie rozwiązała albo wskazała zakres zabroniony (pin IP, ADR-0020).

    Osobno od :attr:`POLACZENIE`, bo to NIE jest problem z silnikiem: żądanie nie opuściło
    maszyny. Bez tej kategorii blokada anty-SSRF trafiała do „zła odpowiedź" i operator
    dostawał radę o formacie OpenAI zamiast o DNS — wykryte przeglądem adwersaryjnym.
    """

    BUDZET_SONDY = "budzet-sondy"
    """Model wyczerpał limit tokenów SONDY, nie zwracając treści. Wina limitu, nie modelu."""

    INNY = "inny"
    """Nierozpoznana przyczyna. Osobna kategoria, żeby nie udawać, że wiemy więcej."""


@dataclass(frozen=True, slots=True)
class OdpowiedzModelu:
    """Wynik REALNEGO zapytania do modelu (sonda głęboka).

    Attributes:
        odpowiedzial: Czy backend zwrócił poprawną odpowiedź w formacie OpenAI.
        tresc: Treść odpowiedzi, przycięta. Może być PUSTA mimo ``odpowiedzial=True`` —
            to osobne ustalenie, nie sukces (patrz :func:`_kontrola_odpowiedzi`).
        powod: Kategoria niepowodzenia; ``None``, gdy model odpowiedział.
        sekundy: Ile trwało żądanie. Mierzone, bo sonda daje modelowi co najmniej tyle czasu,
            co router, a bywa że więcej: bez pomiaru model odpowiadający po 90 s dostałby „OK",
            choć router przerwie go po 60 s. Fałszywe OK jest w diagnozie gorsze niż fałszywy
            alarm.
        powod_zakonczenia: ``finish_reason`` z odpowiedzi backendu. Potrzebny, żeby odróżnić
            „model nic nie wygenerował" od „model nie zmieścił się w limicie tokenów SONDY" —
            drugie jest ograniczeniem pomiaru, nie awarią modelu, a bez tego pola diagnoza
            zgłaszałaby jako wadę modelu skutek własnego ustawienia.
    """

    odpowiedzial: bool
    tresc: str = ""
    powod: PowodOdmowy | None = None
    sekundy: float = 0.0
    powod_zakonczenia: str | None = None


@runtime_checkable
class SondaGleboka(Protocol):
    """Zadaje modelowi PRAWDZIWE pytanie — jedyna kontrola skutku, nie deklaracji.

    **Dlaczego to jest osobny protokół, a nie metoda w** :class:`Sonda`. Po pierwsze:
    opt-in ma być STRUKTURALNY. Diagnoza bez tego obiektu nie może przypadkiem zapytać
    modelu, bo nie ma czym — a zapytanie ma skutki uboczne (ładuje wagi do pamięci,
    potrafi trwać minuty). Flaga logiczna dawałaby ten sam efekt tylko dopóty, dopóki
    nikt jej nie przeoczy. Po drugie: dopisanie metody do :class:`Sonda` unieważniłoby
    każdą istniejącą implementację, w tym testowe — a te nie mają powodu umieć pytać modelu.

    Sondowanie katalogu (:meth:`Sonda.modele_u_dostawcy`) odpowiada na pytanie „czy silnik
    WYMIENIA ten model". To deklaracja. Model wymieniony potrafi nie wstać (nie mieści się
    w pamięci), a endpoint potrafi wymieniać modele, których nie serwuje. Ta sonda pyta
    o skutek: czy da się dostać odpowiedź.
    """

    def zapytaj_model(self, model_id: str, spec: ModelSpec) -> OdpowiedzModelu:
        """Wysyła do modelu minimalne żądanie uzupełnienia i zwraca wynik.

        Implementacja MUSI przechodzić przez tę samą bramkę egress i ten sam klient,
        z których korzysta router — inaczej sprawdzałaby inną drogę niż ta, która
        realnie zawodzi w czacie.
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


def _role_modeli(config: HusarzConfig) -> dict[str, list[str]]:
    """Mapuje model → role, w których jest używany.

    Trzy role, bo trzy niezależne drogi mogą być martwe niezależnie od siebie:
    tryb czatu (``/api/chat``), orkiestracja (``/api/orchestrate``, model domyślny) oraz
    poszczególni agenci (``routing.agent_models``). Na świeżej instalacji czat potrafi
    działać, a orkiestracja być martwa — i odwrotnie.

    Grupujemy PO MODELU, nie po roli: siedmiu agentów wskazujących ten sam model dałoby
    siedem identycznych ustaleń, co utopiłoby diagnozę w powtórzeniach.

    Args:
        config: Wczytana konfiguracja.

    Returns:
        Model → posortowane nazwy ról, które go używają.
    """
    role: dict[str, list[str]] = {}

    def dopisz(model_id: str, rola: str) -> None:
        role.setdefault(model_id, []).append(rola)

    dopisz(config.models.chat or config.models.default, "tryb czatu")
    dopisz(config.models.default, "orkiestracja")
    for agent, model_id in sorted(config.routing.agent_models.items()):
        dopisz(model_id, f"agent {agent}")
    return {m: sorted(set(r)) for m, r in role.items()}


def _kontrola_modelu(
    model_id: str,
    role: Sequence[str],
    config: HusarzConfig,
    sonda: Sonda,
    pamiec: dict[str, list[str] | None],
    gleboka: SondaGleboka | None = None,
) -> list[Ustalenie]:
    """Sprawdza JEDEN model: czy jest w rejestrze, włączony, z endpointem i u dostawcy.

    Args:
        model_id: Identyfikator modelu z rejestru.
        role: Role, w których ten model jest używany (do komunikatu).
        config: Konfiguracja.
        sonda: Dostęp do świata zewnętrznego.
        pamiec: Wyniki sondowania per endpoint — kilka modeli często dzieli jeden silnik,
            a diagnoza nie ma powodu pytać go wielokrotnie.
        gleboka: Sonda zadająca modelowi realne pytanie; ``None`` = bez pytania (domyślnie).

    Returns:
        Ustalenia dotyczące tego modelu.
    """
    gdzie = ", ".join(role)
    spec = config.models.registry.get(model_id)
    if spec is None:
        # Walidacja schematu pilnuje WSZYSTKICH odwołań do modeli — `models.chat`,
        # `models.default`, `routing.agent_models` i `routing.rules` (sprawdzone).
        # Przez `load_config` tu więc nie wejdziemy. Gałąź zostaje, bo `zdiagnozuj`
        # przyjmuje DOWOLNY obiekt konfiguracji — także zbudowany programowo — a diagnoza,
        # która sama wywala się na `None`, byłaby bezużyteczna dokładnie wtedy, gdy jest
        # potrzebna.
        return [
            Ustalenie(
                id=f"model-{model_id}",
                stan=Stan.PROBLEM,
                waga=Waga.BLOKUJACA,
                opis=f"Model '{model_id}' ({gdzie}) nie istnieje w rejestrze modeli.",
                naprawa=(
                    f"Dodaj '{model_id}' do `models.registry` albo popraw odwołanie. "
                    f"Dostępne: {', '.join(sorted(config.models.registry))}."
                ),
            )
        ]

    ustalenia: list[Ustalenie] = []
    if not spec.enabled:
        ustalenia.append(
            Ustalenie(
                id=f"model-{model_id}-wlaczony",
                stan=Stan.PROBLEM,
                waga=Waga.BLOKUJACA,
                opis=f"Model '{model_id}' ({gdzie}) jest WYŁĄCZONY (`enabled: false`).",
                naprawa=f"Ustaw `enabled: true` dla '{model_id}' albo wskaż inny model.",
            )
        )

    if spec.endpoint is None:
        ustalenia.append(
            Ustalenie(
                id=f"model-{model_id}-u-dostawcy",
                stan=Stan.PROBLEM,
                waga=Waga.BLOKUJACA,
                opis=(
                    f"Model '{model_id}' ({gdzie}, backend: {spec.backend}) nie ma endpointu "
                    f"— nie ma dokąd wysłać żądania."
                ),
                naprawa=f"Ustaw `endpoint` dla '{model_id}' w config/models.yaml.",
            )
        )
        return ustalenia

    odmowa = sonda.powod_odmowy_sondowania(spec.endpoint)
    if odmowa is not None:
        ustalenia.append(
            Ustalenie(
                id=f"model-{model_id}-u-dostawcy",
                stan=Stan.NIEZNANY,
                waga=Waga.BLOKUJACA,
                opis=(
                    f"Nie sprawdzono modelu '{model_id}' ({gdzie}): polityka egress nie "
                    f"pozwala odpytać {spec.endpoint} ({odmowa})."
                ),
                naprawa=(
                    "Router ODMÓWI temu modelowi z tego samego powodu. Dodaj host do "
                    "`security.egress.allowlist` albo wskaż endpoint lokalny. W profilu "
                    "airgap zdalny endpoint jest zabroniony z założenia."
                ),
            )
        )
        return ustalenia

    if spec.endpoint not in pamiec:
        pamiec[spec.endpoint] = sonda.modele_u_dostawcy(spec.endpoint)
    dostepne = pamiec[spec.endpoint]

    if dostepne is None:
        ustalenia.append(
            Ustalenie(
                id=f"model-{model_id}-u-dostawcy",
                stan=Stan.NIEZNANY,
                waga=Waga.BLOKUJACA,
                opis=(
                    f"Silnik pod {spec.endpoint} nie odpowiedział — nie wiadomo, czy model "
                    f"'{spec.model}' ({gdzie}) jest dostępny."
                ),
                naprawa=(
                    "Uruchom silnik (np. `ollama serve` albo serwer vLLM) i sprawdź, czy "
                    "endpoint w config/models.yaml wskazuje właściwy port. "
                    "Instrukcja: ollama/README.md."
                ),
            )
        )
    elif _pasuje(spec.model, dostepne):
        ustalenia.append(
            Ustalenie(
                id=f"model-{model_id}-u-dostawcy",
                stan=Stan.OK,
                waga=Waga.INFORMACJA,
                opis=f"Model '{spec.model}' ({gdzie}) jest dostępny pod {spec.endpoint}.",
            )
        )
        # Sonda głęboka WYŁĄCZNIE po potwierdzeniu obecności w katalogu. Pytanie modelu,
        # którego silnik nie wymienia, powtórzyłoby ustalenie wyżej innymi słowami i wysłało
        # żądanie skazane na porażkę. Kontrola droga (ładuje wagi) ma dotyczyć przypadku,
        # w którym katalog mówi „jest", a czat i tak zawodzi.
        if gleboka is not None and spec.enabled:
            ustalenia += _sonduj_glebiej(model_id, spec, gdzie, gleboka)
    else:
        ustalenia.append(
            Ustalenie(
                id=f"model-{model_id}-u-dostawcy",
                stan=Stan.PROBLEM,
                waga=Waga.BLOKUJACA,
                opis=(
                    f"Silnik odpowiada, ale NIE MA modelu '{spec.model}' ({gdzie}). "
                    f"Dostępne: {', '.join(sorted(dostepne)) or '(żadnego)'}."
                ),
                naprawa=(
                    "Przygotuj model wg ollama/README.md (`ollama create ...`) albo zmień "
                    "pole `model` w config/models.yaml na jeden z dostępnych."
                ),
            )
        )
    return ustalenia


# Pytanie sondy. Świadomie NIE jest instrukcją ani prośbą o działanie: model ma tylko
# cokolwiek wygenerować. Krótkie i neutralne, żeby nie zależeć od języka ani od tego,
# czy model ma szablon rozmowy z instrukcjami systemowymi.
_PYTANIE_SONDY = "ping"

# Limit tokenów odpowiedzi. Nie 1–8: model z długą preambułą („rozumowaniem") potrafi
# wyczerpać tak mały budżet, nic nie zwracając, a wtedy diagnoza zgłosiłaby PUSTĄ odpowiedź
# jako problem modelu, choć problemem byłby jej własny limit. 32 to kompromis: wciąż grosze,
# a już poza zasięgiem typowej preambuły.
_MAKS_TOKENOW_SONDY = 32


def _kategoria_bledu(exc: Exception) -> PowodOdmowy:
    """Zamienia błąd backendu na KATEGORIĘ przyczyny, po łańcuchu wyjątków.

    Komunikat transportu jest celowo generyczny („Błąd HTTP przy wywołaniu modelu"), bo
    trafia do odpowiedzi API i do audytu — nie może nieść URL-a ani wnętrzności biblioteki.
    Dla operatora to jednak za mało: timeout, odrzucony klucz i brak pamięci w silniku
    wymagają TRZECH różnych napraw. Sięgamy więc po pierwotną przyczynę przez ``__cause__``
    (transport zachowuje ją przez ``raise ... from exc``) i mapujemy na kategorię —
    **treści wyjątku nigdy nie przepisujemy do wyjścia**.

    Degradacja jest łagodna: nierozpoznany łańcuch daje :attr:`PowodOdmowy.INNY`, a nie
    zgadywanie. Funkcja jest odporna na zmianę wnętrzności transportu — najwyżej straci
    precyzję, nie wywróci diagnozy.

    Args:
        exc: Błąd zgłoszony przez klienta modelu.

    Returns:
        Kategoria przyczyny.
    """
    from husarz.core.errors import EgressError  # noqa: PLC0415

    try:
        import httpx  # noqa: PLC0415
    except ImportError:  # pragma: no cover - httpx jest zależnością rdzenia
        return PowodOdmowy.INNY

    # `ModelBackendError` rzucany przy złym FORMACIE odpowiedzi nie ma przyczyny httpx —
    # rozpoznajemy go po tym, że łańcuch nie prowadzi do wyjątku transportu.
    przyczyna: BaseException | None = exc
    widziane: set[int] = set()
    while przyczyna is not None and id(przyczyna) not in widziane:
        widziane.add(id(przyczyna))
        if isinstance(przyczyna, EgressError):
            # Pin IP / rozwiązywanie nazwy (ADR-0020). Żądanie NIE opuściło maszyny, więc
            # ani „silnik nie odpowiedział", ani „zła odpowiedź" nie byłyby prawdą — a to
            # właśnie robiła pierwsza wersja: radziła sprawdzić format OpenAI przy błędzie DNS.
            return PowodOdmowy.ROZWIAZANIE_NAZWY
        if isinstance(przyczyna, httpx.TimeoutException):
            return PowodOdmowy.TIMEOUT
        if isinstance(przyczyna, httpx.HTTPStatusError):
            kod = przyczyna.response.status_code
            if kod in (401, 403):
                return PowodOdmowy.UWIERZYTELNIENIE
            if kod == 404:
                return PowodOdmowy.NIE_ZNALEZIONO
            if kod >= 500:
                return PowodOdmowy.BLAD_SILNIKA
            return PowodOdmowy.INNY
        if isinstance(przyczyna, httpx.TransportError):
            # Kolejność ma znaczenie: `TimeoutException` też jest `TransportError`,
            # więc sprawdzamy ją WYŻEJ. Tu zostaje reszta: odmowa połączenia, DNS, TLS.
            return PowodOdmowy.POLACZENIE
        if isinstance(przyczyna, ValueError):
            # Transport opakowuje `json.JSONDecodeError` (podklasa `ValueError`) —
            # odpowiedź nie była JSON-em, czyli endpoint nie jest tym, za co się podaje.
            return PowodOdmowy.ZLA_ODPOWIEDZ
        przyczyna = przyczyna.__cause__
    # Brak JAKIEGOKOLWIEK wyjątku httpx w łańcuchu znaczy, że nie zawiodła sieć, tylko
    # parsowanie: `OpenAICompatClient` zgłasza błąd, gdy odpowiedź ma HTTP 200, ale nie ma
    # pól `choices[0].message.content`. Czyli endpoint odpowiada, lecz nie w formacie OpenAI.
    return PowodOdmowy.ZLA_ODPOWIEDZ


# Naprawa zależy od KATEGORII przyczyny, nie od treści wyjątku. Mapa jest jawna, żeby
# dopisanie kategorii wymagało dopisania instrukcji — inaczej nowy przypadek dostałby po
# cichu radę „sprawdź silnik", która bywa myląca (np. przy odrzuconym kluczu API).
_NAPRAWA_ODMOWY: dict[PowodOdmowy, str] = {
    PowodOdmowy.TIMEOUT: (
        "To NIE musi być awaria: model bywa ładowany do pamięci przy pierwszym żądaniu, "
        "a 7B na CPU potrafi liczyć minutę i dłużej. Powtórz z dłuższym limitem "
        "(`--probe-timeout`) albo rozgrzej model jednym żądaniem ręcznie."
    ),
    PowodOdmowy.UWIERZYTELNIENIE: (
        "Silnik odrzucił uwierzytelnienie. Sprawdź `api_key_ref` tego modelu w "
        "config/models.yaml i to, czy sekret pod tą referencją jest rozwiązywalny. "
        "Katalog modeli bywa otwarty, a `/chat/completions` już nie — dlatego kontrola "
        "katalogu tego nie wykryła."
    ),
    PowodOdmowy.NIE_ZNALEZIONO: (
        "Endpoint odpowiedział 404 na `/chat/completions`. Najczęściej `endpoint` wskazuje "
        "na bazę BEZ `/v1` albo na serwer, który wystawia sam katalog modeli."
    ),
    PowodOdmowy.BLAD_SILNIKA: (
        "Silnik przyjął żądanie i przewrócił się na nim. Najczęstsza przyczyna to brak "
        "pamięci na wagi — sprawdź log silnika (`ollama serve`, log vLLM) i rozmiar modelu "
        "względem dostępnego RAM/VRAM."
    ),
    PowodOdmowy.POLACZENIE: (
        "Połączenie nie doszło do skutku, choć chwilę wcześniej silnik wymienił ten model. "
        "Sprawdź, czy proces silnika nie zakończył się między kontrolami."
    ),
    PowodOdmowy.ZLA_ODPOWIEDZ: (
        "Odpowiedź nie ma formatu OpenAI `chat/completions`. Sprawdź, czy `endpoint` na pewno "
        "wskazuje serwer zgodny z OpenAI, a nie np. sam natywny interfejs Ollamy."
    ),
    PowodOdmowy.BRAK_ENDPOINTU: (
        "Model nie ma pola `endpoint` w config/models.yaml — żądanie NIE zostało wysłane, "
        "bo nie ma dokąd. Uzupełnij endpoint."
    ),
    PowodOdmowy.ROZWIAZANIE_NAZWY: (
        "Nazwa hosta się nie rozwiązała albo wskazała zakres zabroniony (pin IP, ADR-0020) — "
        "żądanie NIE opuściło maszyny. To nie jest problem z silnikiem. Sprawdź DNS oraz to, "
        "czy nazwa nie wskazuje na adres metadanych chmury ani na inny zakres infrastrukturalny."
    ),
    PowodOdmowy.BUDZET_SONDY: (
        "To ograniczenie SONDY, nie awaria modelu: model zużył cały limit tokenów sondy na "
        "preambułę i nie zdążył nic napisać. Typowe dla modeli rozumujących. Sprawdź model "
        "ręcznie (`curl` z większym `max_tokens`) — kontrola katalogu i tak przeszła."
    ),
    PowodOdmowy.BRAK_SEKRETU: (
        "Model deklaruje `api_key_ref`, ale sekretu nie udało się rozwiązać — żądanie NIE "
        "zostało wysłane. Ustaw zmienną/plik wskazany referencją. Router zachowa się tak samo "
        "(fail-closed), więc czat zawiedzie z tego samego powodu."
    ),
    PowodOdmowy.EGRESS: (
        "Polityka egress nie pozwoliła wysłać żądania — nic nie poszło do sieci. Dodaj host do "
        "`security.egress.allowlist` albo wskaż endpoint lokalny."
    ),
    PowodOdmowy.INNY: (
        "Przyczyny nie udało się rozpoznać. Sprawdź log silnika i spróbuj wywołać "
        "`/chat/completions` ręcznie (np. `curl`), żeby zobaczyć pełny błąd."
    ),
}

# Ile znaków odpowiedzi pokazujemy. Treść pochodzi od modelu, czyli spoza naszej kontroli —
# do wyjścia diagnozy wpuszczamy jej ograniczony wycinek, w jednej linii.
_MAKS_ZNAKOW_ODPOWIEDZI = 80


def _oczysc(tekst: str) -> str:
    """Przycina treść od modelu i USUWA znaki sterujące, zanim trafi do terminala.

    Samo spłaszczenie białych znaków nie wystarczy. Ustalenia są wypisywane wprost na
    terminal, a odpowiedź modelu to dane spoza naszej kontroli: sekwencja ``\x1b[2J\x1b[H``
    czyści ekran i ustawia kursor w rogu, więc model mógłby wymazać wypisane wyżej problemy
    i domalować własne „[ok]". Diagnoza bezpieczeństwa, której wyjście da się przemalować,
    jest gorsza niż jej brak. Wykryte przeglądem adwersaryjnym, potwierdzone uruchomieniem.

    Zostawiamy wyłącznie znaki drukowalne (kategoria Unicode inna niż ``C*``), zwężamy ciągi
    białych znaków do pojedynczej spacji i przycinamy.

    Args:
        tekst: Surowa treść odpowiedzi modelu.

    Returns:
        Jednolinijkowy, bezpieczny do wypisania wycinek.
    """
    import unicodedata  # noqa: PLC0415

    bez_sterujacych = "".join(
        z for z in tekst if z.isspace() or not unicodedata.category(z).startswith("C")
    )
    return " ".join(bez_sterujacych.split())[:_MAKS_ZNAKOW_ODPOWIEDZI]


# Kategorie, przy których żądanie NIE zostało wysłane. Opis ustalenia musi to mówić wprost —
# inaczej diagnoza twierdzi, że silnik nie odpowiedział, choć nikt go nie pytał.
_NIE_WYSLANO = frozenset(
    {
        PowodOdmowy.BRAK_SEKRETU,
        PowodOdmowy.EGRESS,
        PowodOdmowy.BRAK_ENDPOINTU,
        PowodOdmowy.ROZWIAZANIE_NAZWY,
    }
)


def _sonduj_glebiej(
    model_id: str, spec: ModelSpec, gdzie: str, gleboka: SondaGleboka
) -> list[Ustalenie]:
    """Decyduje, czy modelowi WOLNO zadać prawdziwe pytanie, i zadaje je.

    Backend ``mock`` jest tu wykluczony i to jest istota sprawy: ``MockClient`` odpowiada
    z pamięci, bez sieci i bez modelu. Sonda dostawała od niego „ODPOWIEDZIAŁ" w kilka
    mikrosekund i meldowała OK — czyli JEDYNA kontrola skutku w całej diagnozie nie
    sprawdzała żadnego skutku. Fałszywe OK, i to w mechanizmie, który powstał, żeby fałszywe
    OK wykrywać. Wykryte przeglądem adwersaryjnym, potwierdzone uruchomieniem.

    Model wyłączony (`enabled: false`) nie trafia tu w ogóle — decyduje o tym wołający,
    bo wysyłanie żądania do modelu, o którym linijkę wyżej napisaliśmy „jest WYŁĄCZONY",
    byłoby wewnętrznie sprzeczne.

    Args:
        model_id: Identyfikator modelu z rejestru.
        spec: Wpis modelu.
        gdzie: Role, w których model jest używany.
        gleboka: Sonda zadająca pytanie.

    Returns:
        Ustalenie z sondy albo informacja, dlaczego jej nie uruchomiono.
    """
    if spec.backend is ModelBackend.MOCK:
        return [
            Ustalenie(
                id=f"model-{model_id}-odpowiada",
                stan=Stan.NIEZNANY,
                waga=Waga.INFORMACJA,
                opis=(
                    f"Model '{model_id}' ({gdzie}) ma backend `mock` — sonda głęboka go "
                    f"POMIJA, bo mock odpowiada z pamięci, bez sieci i bez modelu."
                ),
                naprawa=(
                    "Nic do zrobienia, jeśli `mock` jest tu celowy (dev/testy). Jeśli miał "
                    "być prawdziwy silnik — zmień `backend` w config/models.yaml."
                ),
            )
        ]
    return _kontrola_odpowiedzi(model_id, spec, gdzie, gleboka)


def _kontrola_odpowiedzi(
    model_id: str, spec: ModelSpec, gdzie: str, gleboka: SondaGleboka
) -> list[Ustalenie]:
    """Zadaje modelowi realne pytanie i zamienia wynik w ustalenie.

    Rozróżniamy TRZY wyniki, nie dwa, bo „silnik odpowiedział" nie znaczy jeszcze
    „model działa":

    * odpowiedź z treścią → OK,
    * odpowiedź PUSTA → problem (silnik żyje, model nic nie generuje — np. wagi wczytane
      w połowie albo szablon rozmowy nie pasuje do modelu),
    * brak odpowiedzi → problem albo stan NIEZNANY, zależnie od kategorii przyczyny;
      timeout NIE jest dowodem awarii, bo model bywa właśnie ładowany.

    Args:
        model_id: Identyfikator modelu z rejestru.
        spec: Wpis modelu (endpoint, klucz, limity).
        gdzie: Role, w których model jest używany — do komunikatu.
        gleboka: Sonda zadająca pytanie.

    Returns:
        Jedno ustalenie o identyfikatorze ``model-<id>-odpowiada``.
    """
    from husarz.router.client import DEFAULT_TIMEOUT_SECONDS  # noqa: PLC0415

    ident = f"model-{model_id}-odpowiada"
    wynik = gleboka.zapytaj_model(model_id, spec)

    if wynik.odpowiedzial and wynik.tresc.strip():
        czas = f"{wynik.sekundy:.1f} s"
        # Limit EFEKTYWNY, nie samo pole z pliku. `request_timeout_seconds: None` NIE znaczy
        # „bez limitu": klient podstawia wtedy `DEFAULT_TIMEOUT_SECONDS`. Pierwsza wersja
        # porównywała z surowym polem, więc dla KAŻDEGO modelu dostarczonej konfiguracji
        # (żaden go nie ustawia) kontrola była martwa — a operator, który posłuchał rady
        # „powtórz z dłuższym --probe-timeout", dostawał czyste OK dla drogi, którą czat
        # przerywa po 60 s. Wykryte przeglądem adwersaryjnym, potwierdzone uruchomieniem.
        limit = spec.request_timeout_seconds
        efektywny = limit if limit is not None else DEFAULT_TIMEOUT_SECONDS
        if wynik.sekundy > efektywny:
            skad = (
                f"`request_timeout_seconds` dla niego to {limit} s"
                if limit is not None
                else (
                    f"router użyje domyślnych {DEFAULT_TIMEOUT_SECONDS} s "
                    f"(pole `request_timeout_seconds` NIE jest ustawione)"
                )
            )
            dopisz = (
                f"Podnieś `request_timeout_seconds` dla '{model_id}'"
                if limit is not None
                else f"Dopisz `request_timeout_seconds` dla '{model_id}'"
            )
            return [
                Ustalenie(
                    id=ident,
                    stan=Stan.PROBLEM,
                    waga=Waga.BLOKUJACA,
                    opis=(
                        f"Model '{spec.model}' ({gdzie}) odpowiedział, ale dopiero po {czas} "
                        f"— a {skad}. W czacie żądanie zostanie PRZERWANE."
                    ),
                    naprawa=(
                        f"{dopisz} w config/models.yaml na co najmniej "
                        f"{int(wynik.sekundy) + 1} s albo użyj mniejszego modelu. Pierwsze "
                        f"żądanie bywa najwolniejsze (wczytanie wag) — powtórz sondę, żeby "
                        f"zobaczyć czas na rozgrzanym modelu."
                    ),
                )
            ]
        return [
            Ustalenie(
                id=ident,
                stan=Stan.OK,
                waga=Waga.INFORMACJA,
                opis=(
                    f"Model '{spec.model}' ({gdzie}) ODPOWIEDZIAŁ po {czas}: "
                    f'„{_oczysc(wynik.tresc)}".'
                ),
            )
        ]

    if wynik.odpowiedzial:
        # Pusta treść przy `finish_reason: length` to skutek NASZEGO limitu tokenów sondy
        # (model rozumujący potrafi zużyć cały budżet na preambułę), a nie wada modelu.
        # Zgłoszenie tego jako problemu blokującego byłoby obwinianiem modelu za ustawienie
        # narzędzia pomiarowego.
        if wynik.powod_zakonczenia == "length":
            return [
                Ustalenie(
                    id=ident,
                    stan=Stan.NIEZNANY,
                    waga=Waga.OSTRZEZENIE,
                    opis=(
                        f"Model '{spec.model}' ({gdzie}) zużył cały limit {_MAKS_TOKENOW_SONDY} "
                        f"tokenów sondy, nie zwracając treści — nie wiadomo, czy generuje."
                    ),
                    naprawa=_NAPRAWA_ODMOWY[PowodOdmowy.BUDZET_SONDY],
                )
            ]
        return [
            Ustalenie(
                id=ident,
                stan=Stan.PROBLEM,
                waga=Waga.BLOKUJACA,
                opis=(
                    f"Model '{spec.model}' ({gdzie}) przyjął żądanie, ale zwrócił PUSTĄ "
                    f"odpowiedź — czat dostanie to samo."
                ),
                naprawa=(
                    "Sprawdź szablon rozmowy modelu (Modelfile/`chat_template`) i log silnika. "
                    "Pusta odpowiedź przy poprawnym HTTP 200 zwykle znaczy, że model wstał, "
                    "ale nie generuje — najczęściej z powodu niedopasowanego szablonu."
                ),
            )
        ]

    powod = wynik.powod or PowodOdmowy.INNY
    # Timeout to jedyna kategoria „nie wiem": model bywa właśnie ładowany, więc nazwanie tego
    # awarią byłoby zgadywaniem, a sukcesem — kłamstwem.
    nieznany = powod is PowodOdmowy.TIMEOUT
    # Opis MUSI odróżniać „wysłaliśmy i nie dostaliśmy odpowiedzi" od „nic nie wysłaliśmy".
    # Bez tego przy zablokowanym egressie albo braku sekretu diagnoza twierdziła, że silnik
    # nie odpowiedział — choć nikt go nie pytał. Ta sama klasa błędu, co przy kontroli katalogu.
    opis = (
        f"NIE wysłano żądania do modelu '{spec.model}' ({gdzie}) — {powod.value}."
        if powod in _NIE_WYSLANO
        else (
            f"Silnik wymienia model '{spec.model}' ({gdzie}), ale NIE odpowiedział "
            f"na żądanie ({powod.value})."
        )
    )
    return [
        Ustalenie(
            id=ident,
            stan=Stan.NIEZNANY if nieznany else Stan.PROBLEM,
            waga=Waga.BLOKUJACA,
            opis=opis,
            naprawa=_NAPRAWA_ODMOWY[powod],
        )
    ]


def _kontrola_modeli(
    config: HusarzConfig, sonda: Sonda, gleboka: SondaGleboka | None = None
) -> list[Ustalenie]:
    """Sprawdza WSZYSTKIE modele, od których zależy działanie: czat, orkiestracja, agenci.

    Pierwsza wersja sprawdzała wyłącznie model trybu czatu. Na dostarczonej konfiguracji
    dawało to obraz mylący: czat działa na lokalnej Ollamie, więc diagnoza kończyła się
    „ostrzeżeń: 1", podczas gdy orkiestracja i WSZYSTKICH SIEDMIU agentów wskazywało na
    serwery vLLM, których nikt nie uruchomił. Diagnoza opisująca fragment rzeczywistości
    jako całość jest tą samą klasą błędu, co diagnoza mówiąca nieprawdę.
    """
    pamiec: dict[str, list[str] | None] = {}
    ustalenia: list[Ustalenie] = []
    for model_id, role in sorted(_role_modeli(config).items()):
        ustalenia += _kontrola_modelu(model_id, role, config, sonda, pamiec, gleboka)
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
    config: HusarzConfig,
    *,
    sonda: Sonda,
    host: str = "127.0.0.1",
    port: int = 8000,
    sonda_gleboka: SondaGleboka | None = None,
) -> list[Ustalenie]:
    """Uruchamia komplet kontroli i zwraca ustalenia posortowane wg wagi.

    Args:
        config: Wczytana konfiguracja.
        sonda: Dostęp do świata zewnętrznego (sieć, system plików).
        host: Adres, na którym stanie (albo stoi) Husarz — do wykrycia kolizji portu.
        port: Port jw.
        sonda_gleboka: Gdy podana, każdy model potwierdzony w katalogu dostaje REALNE
            żądanie uzupełnienia (`husarz doctor --probe`). Domyślnie ``None``, bo
            kontrola ma skutki uboczne: ładuje wagi do pamięci i potrafi trwać minuty.
            Opt-in jest strukturalny — bez tego obiektu diagnoza nie ma czym zapytać.

    Returns:
        Ustalenia: najpierw problemy blokujące, na końcu informacje. Lista NIGDY nie jest
        pusta — brak problemów też jest ustaleniem, które operator ma zobaczyć.
    """
    ustalenia: list[Ustalenie] = []
    ustalenia += _kontrola_modeli(config, sonda, sonda_gleboka)
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

    Klasa spełnia OBA protokoły — :class:`Sonda` (katalog, katalogi na dysku) i
    :class:`SondaGleboka` (realne żądanie do modelu). To, czy diagnoza pyta model,
    zależy WYŁĄCZNIE od tego, czy wołający przekaże ją jako ``sonda_gleboka``.

    Args:
        config: Konfiguracja — potrzebna do polityki egress i do sekretów modeli.
        timeout: Sekundy na odpowiedź KATALOGU silnika. Krótki celowo: ta kontrola ma być
            szybka, a brak odpowiedzi w kilka sekund i tak znaczy „coś jest nie tak".
        timeout_zapytania: Sekundy na odpowiedź MODELU w sondzie głębokiej. Osobny i o rząd
            wielkości dłuższy, bo pierwsze żądanie wczytuje wagi do pamięci — model 7B na CPU
            potrafi milczeć minutę, zanim odpowie. Wspólny limit zmusiłby do wyboru między
            wolną kontrolą katalogu a fałszywym timeoutem sondy.
        secrets: Dostawca sekretów do rozwiązania `api_key_ref` modelu. ``None`` = brak,
            co dla modelu WYMAGAJĄCEGO klucza kończy się jawnym błędem (fail-closed), a nie
            cichą próbą połączenia bez uwierzytelnienia.
        postep: Wywoływane tuż PRZED wysłaniem żądania, z identyfikatorem modelu. Bez tego
            `husarz doctor --probe` milczał przez kilkadziesiąt sekund na model — narzędzie
            do diagnozowania zawieszeń samo wyglądało na zawieszone, a operator nie wiedział,
            czy czekać, czy przerwać.
    """

    def __init__(
        self,
        config: HusarzConfig,
        *,
        timeout: float = 3.0,
        timeout_zapytania: int = 60,
        secrets: SecretsProvider | None = None,
        postep: Callable[[str], None] | None = None,
    ) -> None:
        self._config = config
        self._timeout = timeout
        self._timeout_zapytania = timeout_zapytania
        self._secrets = secrets
        self._postep = postep

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

    def zapytaj_model(self, model_id: str, spec: ModelSpec) -> OdpowiedzModelu:
        """Wysyła do modelu minimalne żądanie uzupełnienia — kontrola SKUTKU.

        **Świadomie używamy `build_client`, a nie własnego wywołania HTTP.** Sonda ma
        sprawdzać tę drogę, która realnie zawodzi w czacie: ten sam klient, ten sam pin IP
        (ADR-0020), to samo rozwiązywanie `api_key_ref`, ten sam format żądania. Własny,
        „prostszy" strzał sprawdzałby drogę, której nikt nie używa — czyli byłby dokładnie
        tą klasą pomiaru, przed którą ostrzega ten projekt.

        **Świadomie NIE używamy `ModelRouter`.** Router ma fallbacki: przy modelu, który nie
        odpowiada, dostalibyśmy odpowiedź z INNEGO modelu i uznali ją za dowód sprawności
        tego. Diagnoza musi zapytać dokładnie ten model, którego dotyczy.

        Args:
            model_id: Identyfikator modelu z rejestru.
            spec: Wpis modelu.

        Returns:
            Wynik zapytania: czy odpowiedział, co odpowiedział, jak długo to trwało,
            a przy niepowodzeniu — KATEGORIA przyczyny (nigdy surowa treść wyjątku).
        """
        import time  # noqa: PLC0415

        if spec.endpoint is None:
            return OdpowiedzModelu(odpowiedzial=False, powod=PowodOdmowy.BRAK_ENDPOINTU)
        # Bramka egress SPRAWDZANA PONOWNIE, mimo że wołający pytał o nią przy katalogu:
        # metoda jest publiczna, a wyjście do sieci nie może zależeć od tego, czy ktoś
        # pamiętał zapytać wcześniej.
        if self.powod_odmowy_sondowania(spec.endpoint) is not None:
            return OdpowiedzModelu(odpowiedzial=False, powod=PowodOdmowy.EGRESS)

        from husarz.router.client import HttpxTransport, build_client  # noqa: PLC0415
        from husarz.router.errors import ModelBackendError  # noqa: PLC0415
        from husarz.router.types import ChatMessage, ChatRequest  # noqa: PLC0415

        # Limit sondy NIGDY nie może być NIŻSZY od produkcyjnego. Pierwsza wersja narzucała
        # `--probe-timeout` bezwarunkowo, więc dla modelu z `request_timeout_seconds: 120`
        # i domyślną sondą (60 s) diagnoza meldowała timeout, choć router by poczekał —
        # fałszywy alarm z myląca radą. Bierzemy większą z dwóch wartości: sonda ma być
        # co najmniej tak cierpliwa jak router.
        limit_sondy = max(self._timeout_zapytania, spec.request_timeout_seconds or 0)
        # Narzucamy go przez KOPIĘ specyfikacji, bo klient bierze limit z niej, a nie
        # z transportu (wartość klienta jest przekazywana transportowi przy wywołaniu).
        # Kopia, nie mutacja: `spec` należy do wczytanej konfiguracji i jest współdzielona
        # z routerem — narzędzie pomiarowe nie może zmieniać tego, co mierzy.
        spec_sondy = spec.model_copy(update={"request_timeout_seconds": limit_sondy})
        try:
            klient = build_client(
                spec_sondy,
                model_id,
                secrets=self._secrets,
                transport=HttpxTransport(limit_sondy),
            )
        except ModelBackendError:
            # Znany powód: nierozwiązywalny `api_key_ref`. Nic nie zostało wysłane — to inna
            # naprawa niż „silnik odrzucił klucz".
            return OdpowiedzModelu(odpowiedzial=False, powod=PowodOdmowy.BRAK_SEKRETU)
        except Exception:  # noqa: BLE001 - diagnoza NIE MOŻE wywrócić się na wyjątku
            # Pierwsza wersja łapała tu wyłącznie `ModelBackendError`, opierając się na
            # komentarzu „jedyny powód, dla którego build_client zawodzi". Twierdzenie było
            # nieuprawnione: fabryka woła kod dostawcy sekretów, a ten może zgłosić cokolwiek
            # (błąd wejścia-wyjścia, błąd Vaulta). Wyjątek wywracał wtedy CAŁĄ diagnozę.
            return OdpowiedzModelu(odpowiedzial=False, powod=PowodOdmowy.INNY)

        zadanie = ChatRequest(
            messages=[ChatMessage("user", _PYTANIE_SONDY)],
            max_tokens=_MAKS_TOKENOW_SONDY,
            temperature=0.0,
        )
        if self._postep is not None:
            self._postep(model_id)
        start = time.monotonic()
        try:
            odpowiedz = klient.chat(zadanie)
        except ModelBackendError as exc:
            return OdpowiedzModelu(
                odpowiedzial=False,
                powod=_kategoria_bledu(exc),
                sekundy=time.monotonic() - start,
            )
        except Exception:  # noqa: BLE001 - diagnoza NIE MOŻE wywrócić się na wyjątku
            # Nieprzewidziany wyjątek to „nie wiem", nie „model działa". Narzędzie, które
            # samo się przewraca, jest bezużyteczne dokładnie wtedy, gdy jest potrzebne.
            # Treści nie logujemy — mogłaby nieść URL z parametrami.
            return OdpowiedzModelu(
                odpowiedzial=False,
                powod=PowodOdmowy.INNY,
                sekundy=time.monotonic() - start,
            )
        return OdpowiedzModelu(
            odpowiedzial=True,
            tresc=odpowiedz.content,
            sekundy=time.monotonic() - start,
            powod_zakonczenia=odpowiedz.finish_reason,
        )

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
