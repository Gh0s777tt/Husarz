"""Materializacja przebiegu agenta — struktury danych (Etap 16).

Husarz nie miał dotąd ŻADNEGO sposobu, by odpowiedzieć na pytanie „czy ta zmiana promptu,
routingu albo modelu poprawiła działanie agenta". Bez liczby każda optymalizacja jest
zgadywaniem. Trzy niezależne oceny narzędzi zewnętrznych wskazały ten sam brak (ADR-0022).

**Najważniejsza decyzja projektowa: rekord niesie METRYKI, nie TREŚĆ.**

Nie zapisujemy promptów, odpowiedzi modelu ani argumentów narzędzi — wyłącznie to, co da się
policzyć: rodzaj tury, nazwę narzędzia, czy się powiodło, długości i zużycie tokenów. Powody,
w kolejności ważności:

1. **Bezpieczeństwo z konstrukcji, nie z konfiguracji.** Przebieg agenta to najwrażliwszy
   materiał w całym systemie — treść zadania użytkownika, wyniki narzędzi, fragmenty plików.
   Struktura, która fizycznie nie ma pola na tekst, nie wycieknie go przez błąd, złe domyślne
   ustawienie ani przez zrzut ekranu w dokumentacji.
2. **To wystarcza do celu.** Cztery weryfikatory zaplanowane w Etapie 16 (kod wyjścia
   ``run_tests``, udział tur niesparsowalnych, zgodność routingu, blokady bezpieczeństwa)
   potrzebują wyłącznie metryk. Zapis treści nie przybliżyłby ich ani o krok.
3. **Rozmiar.** Metryki mieszczą się w kilkuset bajtach na przebieg; transkrypcje rosłyby
   liniowo z ruchem i wymagałyby retencji, szyfrowania i polityki kasowania.

Gdyby kiedyś okazał się potrzebny zapis transkrypcji (np. dla sędziego LLM), musi to być
osobny, jawnie włączany magazyn z szyfrowaniem at-rest i retencją — NIE rozszerzenie tej
struktury. Patrz `docs/BEZPIECZENSTWO.md`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class StepKind(StrEnum):
    """Rodzaj tury modelu w pętli narzędziowej.

    Odpowiada ``husarz.agents.react.ParseKind``, ale jest osobnym typem: tamten opisuje wynik
    PARSOWANIA, ten — zdarzenie w PRZEBIEGU. Sklejenie ich związałoby format pomiarowy
    z detalem implementacyjnym parsera.
    """

    FINAL = "final"  # model odpowiedział ostatecznie
    ACTION = "action"  # model poprawnie poprosił o narzędzie
    MALFORMED = "malformed"  # marker akcji był, ale format niepoprawny


class Termination(StrEnum):
    """Powód zakończenia przebiegu — bez tego nie da się odróżnić sukcesu od poddania się."""

    FINAL = "final"  # agent odpowiedział — jedyne zakończenie „normalne"
    ITERATION_LIMIT = "iteration_limit"  # wyczerpał ``max_iterations``
    BUDGET = "budget"  # wyczerpał globalny budżet wywołań narzędzi
    ROE_REFUSED = "roe_refused"  # agent wymaga ROE — pętla niedostępna


@dataclass(slots=True, frozen=True)
class RunStep:
    """Jedna tura modelu w przebiegu.

    Attributes:
        index: numer tury liczony od zera.
        kind: rodzaj tury (odpowiedź końcowa / akcja / niesparsowalna).
        model: identyfikator modelu, który odpowiedział w tej turze.
        tool: nazwa narzędzia dla ``kind=ACTION``; pusty łańcuch w pozostałych.
        action: nazwa akcji narzędzia; pusty łańcuch, gdy nie dotyczy.
        ok: czy wywołanie narzędzia się powiodło; ``None``, gdy tura nie wołała narzędzia.
        denied: czy wywołanie odrzuciła bramka (allowlista agenta) — sygnał bezpieczeństwa.
        output_chars: długość odpowiedzi modelu w znakach (miara objętości, nie treść).
        total_tokens: zużycie zgłoszone przez backend w tej turze; ``0``, gdy nie raportuje.
    """

    index: int
    kind: StepKind
    model: str = ""
    tool: str = ""
    action: str = ""
    ok: bool | None = None
    denied: bool = False
    output_chars: int = 0
    total_tokens: int = 0


@dataclass(slots=True)
class RunRecord:
    """Pełny przebieg jednego agenta w odpowiedzi na jedno zadanie.

    ``run_id`` wiąże przebieg z resztą śladu (audyt, orkiestracja). ``principal`` powtarza
    referencję konta z audytu, żeby pomiar dało się filtrować po zleceniodawcy bez sklejania
    dwóch źródeł.

    Attributes:
        run_id: identyfikator przebiegu (nadawany przez wywołującego, nie losowany tutaj —
            dzięki temu struktura pozostaje deterministyczna i testowalna offline).
        agent: nazwa agenta.
        principal: referencja zleceniodawcy (``user:<id>`` / ``token:<rola>``); pusta bez auth.
        task_chars: długość zadania w znakach — NIE jego treść.
        steps: kolejne tury.
        termination: powód zakończenia.
        total_tokens: suma zużycia ze wszystkich tur.
    """

    run_id: str
    agent: str
    principal: str = ""
    task_chars: int = 0
    # Oba rodzaje rekordów dzielą jeden plik — czytelnik musi je odróżnić bez zgadywania.
    record_type: str = "agent"
    steps: list[RunStep] = field(default_factory=list)
    termination: Termination = Termination.FINAL
    total_tokens: int = 0

    @property
    def malformed_ratio(self) -> float:
        """Udział tur niesparsowalnych — miara tego, jak model radzi sobie z protokołem.

        Returns:
            Wartość z zakresu 0.0–1.0; ``0.0`` dla przebiegu bez tur (brak dzielenia przez zero).
        """
        if not self.steps:
            return 0.0
        malformed = sum(1 for s in self.steps if s.kind is StepKind.MALFORMED)
        return malformed / len(self.steps)

    @property
    def tool_calls(self) -> int:
        """Liczba wywołań, które FAKTYCZNIE dotarły do narzędzia.

        Odrzucone przez bramkę nie są tu liczone: instancja narzędzia nigdy nie została
        wywołana, więc doliczanie ich zawyżałoby mianownik wskaźnika awaryjności.
        """
        return sum(1 for s in self.steps if s.ok is not None and not s.denied)

    @property
    def failed_tool_calls(self) -> int:
        """Liczba wywołań narzędzi zakończonych niepowodzeniem SAMEGO narzędzia.

        Odmowa bramki NIE jest awarią narzędzia — jest sukcesem polityki. Wliczanie jej
        sprawiałoby, że wskaźnik awaryjności rośnie wprost proporcjonalnie do skuteczności
        allowlisty, czyli metryka nagradzałaby słabsze zabezpieczenie.
        """
        return sum(1 for s in self.steps if s.ok is False and not s.denied)

    @property
    def denied_tool_calls(self) -> int:
        """Liczba wywołań odrzuconych przez allowlistę agenta (warstwa L1).

        UWAGA na zakres: liczone są WYŁĄCZNIE odmowy allowlisty. Blokady warstw niższych —
        egress, pinowanie IP, polityka konektora wtyczki — docierają do narzędzia i wracają
        jako zwykłe ``ok=False``, więc trafiają do :attr:`failed_tool_calls`. Patrz
        `docs/EWALUACJA.md`, sekcja o granicach pomiaru.
        """
        return sum(1 for s in self.steps if s.denied)


@dataclass(slots=True)
class OrchestrationRecord:
    """Przebieg CAŁEJ orkiestracji: plan, delegacje, rundy refleksji, synteza.

    :class:`RunRecord` mierzy pojedynczego agenta. Ten rekord mierzy warstwę wyżej — jakość
    PLANU, której inaczej nie widać. To nie jest teoretyczna potrzeba: przy realnym modelu 7B
    planista potrafi wskazać agenta, który nie istnieje (np. „Kanclerz" zamiast „kanclerz"),
    a orkiestrator fail-closed pomija taki krok. Bez pomiaru wygląda to jak krótsza odpowiedź,
    nie jak wada planowania.

    Obowiązuje ta sama zasada co w :class:`RunRecord`: METRYKI, nie TREŚĆ. Ani zadania, ani
    planu, ani syntezy nie zapisujemy — tylko liczby.

    Attributes:
        run_id: identyfikator orkiestracji, wspólny z :class:`RunRecord` jej agentów.
        principal: referencja zleceniodawcy; pusta bez uwierzytelnienia.
        task_chars: długość zadania w znakach.
        plan_steps: liczba kroków z PIERWOTNEGO planu (po twardym capie).
        delegations: liczba WSZYSTKICH delegacji, łącznie z krokami z rund refleksji —
            dlatego bywa większa niż ``plan_steps``.
        plan_unknown_agent: kroki PLANU wskazujące agenta, którego nie ma — mianownikiem
            jakości planu jest ``plan_steps``, więc kroki z refleksji tu nie wchodzą.
        skipped_unknown_agent: to samo, ale dla WSZYSTKICH delegacji (plan + refleksja).
        skipped_roe: kroki pominięte, bo agent wymaga ROE, a runtime ROE nie jest wpięty.
        roe_denied: kroki odrzucone przez bramkę ROE mimo wpiętego runtime'u.
        rounds: liczba dodatkowych rund refleksji.
        answer_chars: długość syntezy w znakach.
        total_tokens: zużycie CAŁEJ orkiestracji.
        record_type: dyskryminator rodzaju rekordu w pliku JSONL.
    """

    run_id: str
    principal: str = ""
    task_chars: int = 0
    plan_steps: int = 0
    delegations: int = 0
    plan_unknown_agent: int = 0
    skipped_unknown_agent: int = 0
    skipped_roe: int = 0
    roe_denied: int = 0
    rounds: int = 0
    answer_chars: int = 0
    total_tokens: int = 0
    record_type: str = "orchestration"

    @property
    def plan_validity(self) -> float:
        """Udział kroków PIERWOTNEGO planu, które wskazały istniejącego agenta.

        Kroki dodane przez refleksję są celowo poza mianownikiem: powstają w innej fazie,
        na podstawie już zebranych obserwacji, więc mieszanie ich z planem zaciera to, co
        chcemy mierzyć — trafność planisty na starcie.

        Returns:
            0.0–1.0; ``1.0`` dla pustego planu (brak kroków = brak błędnych kroków).
            To najczystsza miara jakości planisty, jaką da się policzyć bez sędziego.
        """
        if not self.plan_steps:
            return 1.0
        return max(0.0, (self.plan_steps - self.plan_unknown_agent) / self.plan_steps)
