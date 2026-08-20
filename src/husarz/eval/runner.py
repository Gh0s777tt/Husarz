"""Wykonanie zestawu ewaluacyjnego — weryfikatory deterministyczne (Etap 16).

Każdy weryfikator jest CZYSTY względem sieci: nie woła modelu, nie otwiera gniazd, nie
potrzebuje GPU. Dzięki temu ``husarz eval`` może być bramką w CI, a nie rytuałem, który
odpalimy raz i zapomnimy.

Weryfikator zwraca :class:`CaseResult` — nigdy nie rzuca. Wyjątek w jednym przypadku nie może
przerwać całego zestawu; przypadek ma się wtedy zgłosić jako błąd z komunikatem.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from husarz.agents.base import BaseAgent
from husarz.agents.loader import build_agents
from husarz.agents.react import ACTION_CLOSE, ACTION_OPEN
from husarz.agents.tool_loop import ToolLoop
from husarz.config.evals import EvalCase, EvalSet, RoutingCase, ToolPolicyCase
from husarz.config.schema import HusarzConfig
from husarz.router.selection import select_candidates
from husarz.router.types import ChatRequest, ChatResponse
from husarz.runs import RunRecord, StepKind
from husarz.security.audit import AuditLog
from husarz.tools import InMemoryRagBackend, build_tools
from husarz.tools.dispatch import ToolDispatcher


@dataclass(slots=True, frozen=True)
class CaseResult:
    """Wynik pojedynczego przypadku.

    Attributes:
        name: identyfikator przypadku z zestawu.
        kind: rodzaj weryfikatora.
        passed: czy oczekiwanie zostało spełnione.
        detail: krótkie wyjaśnienie — co otrzymano zamiast oczekiwanego wyniku.
    """

    name: str
    kind: str
    passed: bool
    detail: str = ""


@dataclass(slots=True, frozen=True)
class SetResult:
    """Wynik całego zestawu.

    Attributes:
        name: nazwa zestawu.
        results: wyniki poszczególnych przypadków.
    """

    name: str
    results: list[CaseResult]

    @property
    def passed(self) -> int:
        """Liczba przypadków zdanych."""
        return sum(1 for r in self.results if r.passed)

    @property
    def failed(self) -> int:
        """Liczba przypadków niezdanych."""
        return sum(1 for r in self.results if not r.passed)

    @property
    def ok(self) -> bool:
        """Czy cały zestaw przeszedł (pusty zestaw jest uznawany za zdany)."""
        return self.failed == 0


class _ScriptedRouter:
    """Router zwracający z góry ustaloną prośbę o narzędzie, potem odpowiedź końcową.

    Zastępuje model, żeby weryfikator ``tool_policy`` sprawdzał BRAMKĘ, a nie to, czy model
    zechce dziś poprosić o narzędzie. Bez tego wynik byłby losowy — a bramka bezpieczeństwa
    musi dawać ten sam werdykt zawsze.
    """

    def __init__(self, tool: str, action: str) -> None:
        payload = json.dumps({"tool": tool, "action": action, "args": {}})
        self._responses = [f"{ACTION_OPEN}{payload}{ACTION_CLOSE}", "koniec"]
        self._i = 0

    def complete(self, request: ChatRequest, **kwargs: Any) -> ChatResponse:
        content = self._responses[min(self._i, len(self._responses) - 1)]
        self._i += 1
        return ChatResponse(model="eval-stub", content=content)


class _Collector:
    """Magazyn przebiegów zbierający rekordy w pamięci (ewaluacja nie dotyka dysku)."""

    def __init__(self) -> None:
        self.saved: list[RunRecord] = []

    def save(self, record: RunRecord) -> None:
        self.saved.append(record)


def _check_routing(config: HusarzConfig, case: RoutingCase) -> CaseResult:
    """Sprawdza, czy agent trafi na oczekiwany model (czysta funkcja, bez sieci)."""
    if case.agent not in config.agents:
        return CaseResult(case.name, case.kind, False, f"agent '{case.agent}' nie istnieje")
    candidates = select_candidates(config, agent=case.agent, tags=case.tags or None)
    if not candidates:
        return CaseResult(case.name, case.kind, False, "router nie wskazał żadnego kandydata")
    actual = candidates[0]
    if actual == case.expect_model:
        return CaseResult(case.name, case.kind, True)
    return CaseResult(
        case.name, case.kind, False, f"oczekiwano '{case.expect_model}', wybrano '{actual}'"
    )


def _check_tool_policy(
    config: HusarzConfig, case: ToolPolicyCase, *, agents: dict[str, BaseAgent], workspace: Path
) -> CaseResult:
    """Uruchamia pętlę narzędziową ze skryptowanym routerem i sprawdza werdykt bramki."""
    agent = agents.get(case.agent)
    if agent is None:
        return CaseResult(case.name, case.kind, False, f"agent '{case.agent}' nie istnieje")
    if not agent.config.tool_loop_enabled:
        # Bez opt-inu pętla nie wystartuje i przypadek nie zmierzyłby niczego — mówimy to
        # wprost, zamiast raportować fałszywe „zablokowano".
        return CaseResult(
            case.name, case.kind, False, f"agent '{case.agent}' ma wyłączoną pętlę narzędziową"
        )

    tools = build_tools(config, workspace=workspace, rag_backend=InMemoryRagBackend())
    kind_of = {name: tc.kind for name, tc in config.tools.items()}
    collector = _Collector()
    loop = ToolLoop(
        ToolDispatcher(tools, kind_of), AuditLog(), config.security.tool_loop, runs=collector
    )
    loop.run(
        agent,
        f"[eval] {case.name}",
        router=_ScriptedRouter(case.tool, case.action),
        budget=loop.new_budget(),
        run_id=f"eval:{case.name}",
    )
    if not collector.saved:
        return CaseResult(case.name, case.kind, False, "pętla nie zapisała przebiegu")
    record = collector.saved[0]
    actions = [s for s in record.steps if s.kind is StepKind.ACTION]
    if not actions:
        return CaseResult(case.name, case.kind, False, "pętla nie zarejestrowała wywołania")
    denied = actions[0].denied
    expected_denied = case.expect == "denied"
    if denied == expected_denied:
        return CaseResult(case.name, case.kind, True)
    stan = "zablokowane" if denied else "przepuszczone"
    return CaseResult(case.name, case.kind, False, f"oczekiwano '{case.expect}', było {stan}")


def run_case(
    config: HusarzConfig, case: EvalCase, *, agents: dict[str, BaseAgent], workspace: Path
) -> CaseResult:
    """Wykonuje jeden przypadek. NIGDY nie rzuca — błąd staje się niezdanym przypadkiem.

    Args:
        config: wczytana konfiguracja.
        case: przypadek do sprawdzenia.
        agents: zbudowani agenci (raz na cały zestaw — budowa czyta prompty z dysku).
        workspace: katalog roboczy dla narzędzi (tymczasowy, tworzony przez wywołującego).

    Returns:
        Wynik przypadku.
    """
    try:
        if isinstance(case, RoutingCase):
            return _check_routing(config, case)
        return _check_tool_policy(config, case, agents=agents, workspace=workspace)
    except Exception as exc:  # noqa: BLE001 - jeden zły przypadek nie wywraca zestawu
        return CaseResult(case.name, case.kind, False, f"błąd wykonania: {exc}")


def run_set(
    config: HusarzConfig, eval_set: EvalSet, *, prompts_dir: Path, workspace: Path
) -> SetResult:
    """Wykonuje cały zestaw ewaluacyjny.

    Args:
        config: wczytana konfiguracja.
        eval_set: zestaw przypadków.
        prompts_dir: katalog promptów agentów.
        workspace: katalog roboczy dla narzędzi.

    Returns:
        Wynik zbiorczy zestawu.
    """
    agents = build_agents(config, prompts_dir=prompts_dir)
    return SetResult(
        name=eval_set.name,
        results=[
            run_case(config, case, agents=agents, workspace=workspace) for case in eval_set.cases
        ],
    )
