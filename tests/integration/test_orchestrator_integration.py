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


class ToolLoopRouter:
    """Router: hetman planuje→kopijnik; kopijnik emituje akcję, potem odpowiedź końcową."""

    def __init__(self) -> None:
        self.kopijnik_turns = 0

    def complete(self, request, *, agent=None, model=None, tags=None):  # noqa: ANN001
        content = request.messages[-1].content
        if agent == "husarz":
            if PHASE_PLAN in content:
                return ChatResponse(
                    model="glm-main",
                    content='{"steps": [{"agent": "kopijnik", "task": "zapisz plik a.md"}]}',
                )
            if PHASE_REFLECT in content:
                return ChatResponse(model="glm-main", content='{"done": true}')
            return ChatResponse(model="glm-main", content="Synteza: ukończono.")
        if agent == "kopijnik":
            self.kopijnik_turns += 1
            if self.kopijnik_turns == 1:
                action = (
                    '[[HUSARZ_ACTION]]{"tool":"file_edit","action":"write",'
                    '"args":{"path":"a.md","content":"treść"}}[[/HUSARZ_ACTION]]'
                )
                return ChatResponse(model="mock-kopijnik", content=action)
            return ChatResponse(model="mock-kopijnik", content="Zapisałem plik a.md.")
        return ChatResponse(model=f"mock-{agent}", content=f"[{agent}] wynik")


def test_orchestrator_routes_opt_in_agent_through_tool_loop(
    repo_config_dir: Path, tmp_path: Path
) -> None:
    from husarz.agents.tool_loop import build_tool_loop
    from husarz.security import AuditLog
    from husarz.tools import InMemoryRagBackend

    # Włączamy pętlę dla kopijnika i zawężamy jego allowlistę do file_edit (opt-in).
    config = load_config(
        repo_config_dir,
        runtime_overrides={
            "agents": {"kopijnik": {"tool_loop_enabled": True, "tools": ["file_edit"]}}
        },
    )
    prompts_dir = repo_config_dir.parent / "prompts"
    router = ToolLoopRouter()
    loop = build_tool_loop(
        config, workspace=tmp_path, audit=AuditLog(), rag_backend=InMemoryRagBackend()
    )
    orchestrator = build_orchestrator(config, router, prompts_dir=prompts_dir, tool_loop=loop)

    result = orchestrator.run("Zapisz notatkę.")

    assert (tmp_path / "a.md").read_text(encoding="utf-8") == "treść"  # pętla wykonała narzędzie
    assert result.observations[0].agent == "kopijnik"
    assert result.observations[0].output == "Zapisałem plik a.md."  # odpowiedź końcowa po akcji
    assert router.kopijnik_turns == 2  # akcja + finalizacja (pętla, nie single-shot)
