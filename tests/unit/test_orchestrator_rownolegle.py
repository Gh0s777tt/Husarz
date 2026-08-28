"""Równoległa delegacja niezależnych kroków planu (Etap 18k).

**Dlaczego to jest bezpieczne — i skąd to wiadomo.** Kroki jednej rundy są NIEZALEŻNE:
każdy dostaje ten sam `context` (w pierwszej rundzie `None`, w rundach refleksji podsumowanie
policzone RAZ przed pętlą), więc żaden nie widzi wyniku innego. Nie jest to założenie, tylko
własność odczytana z kodu i utrwalona testem `test_kroki_planu_NIE_widza_sie_nawzajem` —
gdyby ktoś kiedyś przekazał krokom wyniki poprzedników, zrównoleglanie przestałoby być
poprawne i ten test to zatrzyma.

Najważniejszy test to `test_kolejnosc_wyniku_jest_PLANOWA_nie_wyscigowa`. Obserwacje wchodzą
do refleksji i syntezy, a rekord pomiarowy ma być porównywalny między przebiegami — kolejność
zależna od tego, kto pierwszy skończy, uczyniłaby oba nieporównywalnymi.
"""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from husarz.agents import Towarzysz
from husarz.agents.tool_loop import ToolCallBudget
from husarz.config.schema import AgentConfig, HusarzConfig
from husarz.orchestrator import PHASE_PLAN, PHASE_REFLECT, PHASE_SYNTH, Orchestrator
from husarz.orchestrator.husarz import build_orchestrator
from husarz.router import ChatResponse
from husarz.router.types import Usage

pytestmark = pytest.mark.unit

# Nacisk dobrany POMIAREM, nie na oko. Przy 8 wątkach x 50 mutacja usuwająca zamek
# czerwieniła test 4 razy na 5 — a test migoczący jest gorszy niż jego brak, bo uczy
# ignorować czerwień. Przy tych wartościach wykrycie było powtarzalne w każdym z ośmiu
# przebiegów; pomiar zapisany w docs/BEZPIECZENSTWO.md (Etap 18k).
_WATKOW = 16
_ITERACJI = 100
_BUDZET = 200

_PLAN_TRZY = (
    '{"steps": [{"agent": "a", "task": "z1"}, '
    '{"agent": "b", "task": "z2"}, {"agent": "c", "task": "z3"}]}'
)


class _RouterZBariera:
    """Router, w którym specjaliści czekają na barierze — dowód REALNEJ współbieżności.

    Bariera zwalnia dopiero, gdy dojdzie do niej `oczekiwanych` wątków. Przy wykonaniu
    sekwencyjnym pierwszy wątek czekałby w nieskończoność, więc test wywróciłby się na
    limicie czasu zamiast po cichu przejść — to jedyny sposób, by odróżnić „równolegle"
    od „szybko po kolei".
    """

    def __init__(self, plan: str, oczekiwanych: int, *, opoznienia: dict[str, float] | None = None):
        self._plan = plan
        self.bariera = threading.Barrier(oczekiwanych, timeout=10)
        self._opoznienia = opoznienia or {}
        self.kolejnosc_zakonczenia: list[str] = []
        self._zamek = threading.Lock()

    def complete(self, request, *, agent=None, model=None, tags=None):  # noqa: ANN001
        """Odpowiada wg fazy; specjaliści przechodzą przez barierę."""
        tresc = request.messages[-1].content
        if agent == "husarz":
            if PHASE_PLAN in tresc:
                return ChatResponse(model="m", content=self._plan)
            if PHASE_REFLECT in tresc:
                return ChatResponse(model="m", content='{"done": true, "additional_steps": []}')
            if PHASE_SYNTH in tresc:
                return ChatResponse(model="m", content="Synteza.")
        self.bariera.wait()
        if agent in self._opoznienia:
            threading.Event().wait(self._opoznienia[agent])
        with self._zamek:
            self.kolejnosc_zakonczenia.append(str(agent))
        return ChatResponse(
            model=f"m-{agent}",
            content=f"[{agent}]",
            usage=Usage(prompt_tokens=10, completion_tokens=5, total_tokens=15),
        )


def _agenci() -> dict:
    """Hetman plus trzej specjaliści."""
    nazwy = ["husarz", "a", "b", "c"]
    return {n: Towarzysz(AgentConfig(name=n, prompt_file=f"{n}.md"), f"Jesteś {n}.") for n in nazwy}


def test_kroki_planu_biegna_RZECZYWISCIE_rownolegle() -> None:
    """Bariera na trzy wątki zwolni się tylko wtedy, gdy trzy kroki biegną naraz."""
    router = _RouterZBariera(_PLAN_TRZY, oczekiwanych=3)
    orkiestrator = Orchestrator(_agenci(), router, max_parallel=3)

    wynik = orkiestrator.run("Zadanie.")

    assert [o.agent for o in wynik.observations] == ["a", "b", "c"]


def test_kolejnosc_wyniku_jest_PLANOWA_nie_wyscigowa() -> None:
    """Sedno: `a` kończy OSTATNI, a mimo to jest pierwszą obserwacją.

    Bez tego obserwacje wchodziłyby do refleksji i syntezy w kolejności zależnej od
    wyścigu, a rekordy pomiarowe przestałyby być porównywalne między przebiegami.
    """
    router = _RouterZBariera(_PLAN_TRZY, oczekiwanych=3, opoznienia={"a": 0.20, "b": 0.10})
    orkiestrator = Orchestrator(_agenci(), router, max_parallel=3)

    wynik = orkiestrator.run("Zadanie.")

    assert router.kolejnosc_zakonczenia == ["c", "b", "a"], "założenie testu: `a` kończy ostatni"
    assert [o.agent for o in wynik.observations] == ["a", "b", "c"]


def test_domyslnie_wykonanie_jest_SEKWENCYJNE() -> None:
    """Wartość domyślna nie zmienia zachowania — bariera na 2 wątki MUSI się zaciąć.

    Bez tej asercji cały plik przechodziłby także wtedy, gdyby równoległość była
    włączona zawsze, a wtedy `max_parallel_delegations: 1` byłoby polem bez skutku.
    """
    router = _RouterZBariera(_PLAN_TRZY, oczekiwanych=2)
    orkiestrator = Orchestrator(_agenci(), router)  # domyślnie max_parallel=1

    with pytest.raises(threading.BrokenBarrierError):
        orkiestrator.run("Zadanie.")


def test_kroki_planu_NIE_widza_sie_nawzajem() -> None:
    """Warunek poprawności zrównoleglenia, utrwalony jako test.

    Gdyby ktoś kiedyś zaczął przekazywać krokom wyniki poprzedników, zrównoleglanie
    przestałoby być semantycznie neutralne. Ten test to zatrzyma, zanim zdąży zaszkodzić.
    """
    konteksty: list[str | None] = []

    class _Podgladajacy(_RouterZBariera):
        def complete(self, request, *, agent=None, model=None, tags=None):  # noqa: ANN001
            if agent != "husarz":
                # Kontekst trafia do wiadomości SYSTEMOWEJ lub jako ogrodzone dane;
                # sprawdzamy, czy w rozmowie pojawia się wynik innego kroku.
                konteksty.append(" ".join(m.content for m in request.messages))
            return super().complete(request, agent=agent, model=model, tags=tags)

    router = _Podgladajacy(_PLAN_TRZY, oczekiwanych=3)
    Orchestrator(_agenci(), router, max_parallel=3).run("Zadanie.")

    for rozmowa in konteksty:
        assert "[a]" not in rozmowa and "[b]" not in rozmowa and "[c]" not in rozmowa


def test_zuzycie_tokenow_jest_LICZONE_w_calosci() -> None:
    """Sumator pod współbieżnością nie może gubić przyrostów — od niego zależy limit konta."""
    router = _RouterZBariera(_PLAN_TRZY, oczekiwanych=3)
    orkiestrator = Orchestrator(_agenci(), router, max_parallel=3)

    wynik = orkiestrator.run("Zadanie.")

    assert wynik.usage is not None
    # Trzy delegacje po 15 tokenów; fazy hetmana nie raportują zużycia w tym routerze.
    assert wynik.usage.total_tokens == 45


def test_budzet_narzedzi_NIE_jest_przekraczany_rownolegle() -> None:
    """`try_spend` to read-modify-write; bez zamka dwa wątki wydałyby ostatni token."""
    budzet = ToolCallBudget(remaining=_BUDZET)
    wydane: list[bool] = []
    zamek_testu = threading.Lock()

    def praca() -> None:
        # Zbieramy lokalnie i scalamy pod zamkiem TESTU, żeby to nie sam test był
        # źródłem wyścigu, który bada.
        lokalne = [budzet.try_spend() for _ in range(_ITERACJI)]
        with zamek_testu:
            wydane.extend(lokalne)

    watki = [threading.Thread(target=praca) for _ in range(_WATKOW)]
    for w in watki:
        w.start()
    for w in watki:
        w.join()

    assert sum(wydane) == _BUDZET, "budżet został przekroczony albo zgubiono wydania"
    assert budzet.remaining == 0


def test_konfiguracja_DOCHODZI_do_orkiestratora(tmp_path: Path) -> None:
    """Pole konfiguracji musi mieć czytelnika — inaczej byłoby ozdobą.

    Pierwsza wersja tego testu sprawdzała wyłącznie, że schemat przyjmuje wartość — czyli
    NIE badała tego, co obiecywała nazwą. Wykryła to kontrola nośności: mutacja
    zastępująca przewleczenie stałą `max_parallel=1` nie zaczerwieniła niczego. Teraz test
    idzie przez `build_orchestrator`, czyli tę samą drogę, którą idzie aplikacja.
    """
    (tmp_path / "husarz.md").write_text("Jesteś Husarz.", encoding="utf-8")
    config = HusarzConfig.model_validate(
        {
            "models": {"default": "m", "registry": {"m": {"backend": "mock", "model": "m"}}},
            "agents": {"husarz": {"name": "husarz", "prompt_file": "husarz.md"}},
            "platform": {"orchestrator": {"max_parallel_delegations": 4}},
        }
    )

    orkiestrator = build_orchestrator(
        config, _RouterZBariera("{}", oczekiwanych=1), prompts_dir=tmp_path
    )

    assert orkiestrator._max_parallel == 4


def test_wartoscia_DOMYSLNA_jest_wykonanie_sekwencyjne(tmp_path: Path) -> None:
    """Konfiguracja, która niczego nie ustawia, ma zachowywać się jak przed Etapem 18k.

    Zrównoleglenie nie zawsze przyspiesza: kroki planu trafiają często do TEGO SAMEGO
    silnika lokalnego, a jedna karta wykona je i tak po kolei, tyle że przy większym
    zużyciu pamięci. Włączenie tego bez zrozumienia układu sprzętowego potrafi pogorszyć
    czas odpowiedzi — dlatego jest to decyzja operatora, a nie wartość domyślna.
    """
    (tmp_path / "husarz.md").write_text("Jesteś Husarz.", encoding="utf-8")
    config = HusarzConfig.model_validate(
        {
            "models": {"default": "m", "registry": {"m": {"backend": "mock", "model": "m"}}},
            "agents": {"husarz": {"name": "husarz", "prompt_file": "husarz.md"}},
        }
    )

    orkiestrator = build_orchestrator(
        config, _RouterZBariera("{}", oczekiwanych=1), prompts_dir=tmp_path
    )

    assert config.platform.orchestrator.max_parallel_delegations == 1
    assert orkiestrator._max_parallel == 1


def _sprawdz_wzajemne_wykluczanie(obiekt: object, wywolaj) -> None:  # noqa: ANN001
    """Deterministycznie sprawdza, że operacja NIE wchodzi przy trzymanym zamku.

    **Dlaczego tak, a nie przez nacisk współbieżny.** Wyścigu „sprawdź i zmień" nie da się
    wykryć pewnie metodą czarnoskrzynkową: pomiar pokazał 4/5, a po zwiększeniu nacisku
    7/8 przebiegów. Test migoczący jest gorszy niż jego brak, bo uczy ignorować czerwień.
    Tu trzymamy zamek jawnie i sprawdzamy, że operacja się na nim zatrzymuje — co jest
    rozstrzygające w OBIE strony: bez zamka przechodzi natychmiast.

    Args:
        obiekt: Obiekt z polem ``_zamek``.
        wywolaj: Funkcja bezargumentowa wykonująca chronioną operację.
    """
    skonczyl = threading.Event()

    def probuj() -> None:
        wywolaj()
        skonczyl.set()

    with obiekt._zamek:  # type: ignore[attr-defined]  # celowo sięgamy po prywatny zamek
        watek = threading.Thread(target=probuj, daemon=True)
        watek.start()
        assert not skonczyl.wait(0.5), "operacja weszła MIMO trzymanego zamka — brak wykluczania"

    assert skonczyl.wait(5.0), "po zwolnieniu zamka operacja musi dojść do skutku"


def test_budzet_narzedzi_JEST_wzajemnie_wykluczajacy() -> None:
    """Budżet ogranicza amplifikację (spawny kontenerów) — musi być twardy, nie przybliżony."""
    budzet = ToolCallBudget(remaining=5)

    _sprawdz_wzajemne_wykluczanie(budzet, budzet.try_spend)


def test_sumator_zuzycia_JEST_wzajemnie_wykluczajacy() -> None:
    """Na sumatorze opiera się limit tokenów konta — niedoliczenie to przepuszczenie żądania."""
    from husarz.router.types import UsageMeter  # noqa: PLC0415

    meter = UsageMeter()

    _sprawdz_wzajemne_wykluczanie(
        meter, lambda: meter.add(Usage(prompt_tokens=1, completion_tokens=1, total_tokens=2))
    )
