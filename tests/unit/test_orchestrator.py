"""Testy e2e orkiestratora „Husarz" na skryptowanym routerze (bez sieci)."""

from __future__ import annotations

import pytest

from husarz.agents import Towarzysz
from husarz.config.schema import AgentConfig
from husarz.orchestrator import (
    PHASE_PLAN,
    PHASE_REFLECT,
    PHASE_SYNTH,
    Orchestrator,
    OrchestratorError,
)
from husarz.router import ChatResponse

pytestmark = pytest.mark.unit


class ScriptedRouter:
    """Router testowy: odpowiada wg fazy (hetman) lub echem zadania (specjaliści)."""

    def __init__(
        self,
        plan: str,
        reflect: str = '{"done": true, "additional_steps": []}',
        answer: str = "Synteza: gotowe.",
    ) -> None:
        self.calls: list[tuple[str | None, str]] = []
        self._plan = plan
        self._reflect = reflect
        self._answer = answer

    def complete(self, request, *, agent=None, model=None, tags=None):  # noqa: ANN001
        content = request.messages[-1].content
        self.calls.append((agent, content))
        if agent == "husarz":
            if PHASE_PLAN in content:
                return ChatResponse(model="glm-main", content=self._plan)
            if PHASE_REFLECT in content:
                return ChatResponse(model="glm-main", content=self._reflect)
            if PHASE_SYNTH in content:
                return ChatResponse(model="glm-main", content=self._answer)
        return ChatResponse(model=f"mock-{agent}", content=f"[{agent}] {content}")


def _agents() -> dict:
    return {
        "husarz": Towarzysz(AgentConfig(name="husarz", prompt_file="husarz.md"), "Jesteś Husarz."),
        "bielik": Towarzysz(AgentConfig(name="bielik", prompt_file="bielik.md"), "Jesteś Bielik."),
        "kopijnik": Towarzysz(
            AgentConfig(name="kopijnik", prompt_file="kopijnik.md"), "Jesteś Kopijnik."
        ),
    }


def test_orchestrator_end_to_end_multi_agent() -> None:
    plan = (
        '{"steps": [{"agent": "bielik", "task": "Przetłumacz"}, '
        '{"agent": "kopijnik", "task": "Napisz kod"}]}'
    )
    router = ScriptedRouter(plan=plan)
    orchestrator = Orchestrator(_agents(), router)

    result = orchestrator.run("Zbuduj i przetłumacz moduł.")

    assert [o.agent for o in result.observations] == ["bielik", "kopijnik"]
    assert result.observations[0].output == "[bielik] Przetłumacz"
    assert result.observations[1].output == "[kopijnik] Napisz kod"
    assert result.answer == "Synteza: gotowe."
    assert result.rounds == 0
    # Sekwencja faz: plan -> deleguj(bielik, kopijnik) -> refleksja -> synteza.
    assert [a for a, _ in router.calls] == ["husarz", "bielik", "kopijnik", "husarz", "husarz"]


def test_orchestrator_skips_unknown_agent() -> None:
    plan = '{"steps": [{"agent": "nieznany", "task": "x"}, {"agent": "bielik", "task": "y"}]}'
    orchestrator = Orchestrator(_agents(), ScriptedRouter(plan=plan))
    result = orchestrator.run("Zadanie.")
    assert result.observations[0].output.startswith("[pominięto")
    assert result.observations[1].agent == "bielik"


def test_orchestrator_plan_parse_failure_still_synthesizes() -> None:
    orchestrator = Orchestrator(_agents(), ScriptedRouter(plan="to nie jest żaden JSON"))
    result = orchestrator.run("Zadanie.")
    assert result.observations == []  # brak kroków = brak delegacji
    assert result.answer == "Synteza: gotowe."  # synteza i tak następuje


def test_orchestrator_reflection_adds_step() -> None:
    plan = '{"steps": [{"agent": "bielik", "task": "Przetłumacz"}]}'
    reflect = '{"done": false, "additional_steps": [{"agent": "kopijnik", "task": "Popraw"}]}'
    orchestrator = Orchestrator(_agents(), ScriptedRouter(plan=plan, reflect=reflect))
    result = orchestrator.run("Zadanie.")
    assert [o.agent for o in result.observations] == ["bielik", "kopijnik"]
    assert result.rounds == 1


def test_orchestrator_requires_orchestrator_agent() -> None:
    agents = {"bielik": Towarzysz(AgentConfig(name="bielik", prompt_file="b.md"), "x")}
    with pytest.raises(OrchestratorError, match="orkiestratora"):
        Orchestrator(agents, ScriptedRouter(plan="{}"))
