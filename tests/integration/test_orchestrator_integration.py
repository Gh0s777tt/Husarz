"""Test integracyjny: build_orchestrator z realnej konfiguracji i promptów repo.

Router jest skryptowany (bez sieci) — sprawdzamy złożenie ładowarki agentów,
realnych promptów i pętli orkiestratora w jedną całość.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from husarz.config import load_config
from husarz.orchestrator import PHASE_PLAN, PHASE_REFLECT, PHASE_SYNTH, build_orchestrator
from husarz.router import ChatResponse

pytestmark = pytest.mark.integration


class ScriptedRouter:
    def __init__(self, plan: str) -> None:
        self.calls: list[str | None] = []
        self._plan = plan

    def complete(self, request, *, agent=None, model=None, tags=None):  # noqa: ANN001
        content = request.messages[-1].content
        self.calls.append(agent)
        if agent == "husarz":
            if PHASE_PLAN in content:
                return ChatResponse(model="glm-main", content=self._plan)
            if PHASE_REFLECT in content:
                return ChatResponse(model="glm-main", content='{"done": true}')
            if PHASE_SYNTH in content:
                return ChatResponse(model="glm-main", content="Synteza: ukończono.")
        return ChatResponse(model=f"mock-{agent}", content=f"[{agent}] wynik")


def test_build_orchestrator_from_repo_runs(repo_config_dir: Path) -> None:
    config = load_config(repo_config_dir)
    prompts_dir = repo_config_dir.parent / "prompts"
    plan = '{"steps": [{"agent": "bielik", "task": "Przetłumacz opis"}]}'
    router = ScriptedRouter(plan)

    orchestrator = build_orchestrator(config, router, prompts_dir=prompts_dir)
    result = orchestrator.run("Przygotuj i przetłumacz krótki opis.")

    assert result.observations[0].agent == "bielik"
    assert result.observations[0].output == "[bielik] wynik"
    assert result.answer == "Synteza: ukończono."
    assert "husarz" in router.calls  # hetman planował i syntetyzował
