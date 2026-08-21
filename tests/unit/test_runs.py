"""Materializacja przebiegu agenta — model, magazyn, wpięcie w pętlę (Etap 16).

Husarz nie miał dotąd żadnej liczby opisującej jakość pracy agenta. Te testy pilnują, że
rekord przebiegu faktycznie powstaje, niesie właściwe metryki i NIE psuje pracy agenta,
gdy zapis zawiedzie.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from husarz.agents.react import ACTION_CLOSE, ACTION_OPEN
from husarz.agents.tool_loop import ToolLoop
from husarz.agents.towarzysz import Towarzysz
from husarz.config import load_config
from husarz.config.schema import AgentConfig, ToolLoopConfig
from husarz.router.types import ChatRequest, ChatResponse, Usage
from husarz.runs import (
    JsonlRunStore,
    NullRunStore,
    RunRecord,
    RunStep,
    StepKind,
    Termination,
    build_run_store,
)
from husarz.security import AuditLog
from husarz.tools import ExecResult, InMemoryRagBackend, SandboxSpec, build_tools
from husarz.tools.dispatch import ToolDispatcher

pytestmark = pytest.mark.unit


class _Router:
    def __init__(self, responses: list[str], usage: Usage | None = None) -> None:
        self._responses = responses
        self._i = 0
        self._usage = usage

    def complete(self, request: ChatRequest, **kwargs: Any) -> ChatResponse:
        content = self._responses[min(self._i, len(self._responses) - 1)]
        self._i += 1
        return ChatResponse(model="test-model", content=content, usage=self._usage)


class _Executor:
    def run(self, spec: SandboxSpec) -> ExecResult:
        return ExecResult(exit_code=0, stdout="wykonano")


class _CollectingStore:
    """Magazyn w pamięci — pozwala asertować rekord bez dotykania dysku."""

    def __init__(self) -> None:
        self.saved: list[RunRecord] = []

    def save(self, record: RunRecord) -> None:
        self.saved.append(record)


def _action(tool: str, action: str, args: dict[str, Any]) -> str:
    return (
        f"{ACTION_OPEN}{json.dumps({'tool': tool, 'action': action, 'args': args})}{ACTION_CLOSE}"
    )


def _agent(tools: list[str], **cfg: Any) -> Towarzysz:
    base: dict[str, Any] = {
        "name": "kopijnik",
        "display_name": "Kopijnik",
        "agent_class": "towarzysz",
        "prompt_file": "kopijnik.md",
        "tools": tools,
        "tool_loop_enabled": True,
        "max_iterations": 4,
    }
    base.update(cfg)
    return Towarzysz(AgentConfig(**base), "Jesteś Kopijnikiem.")


def _loop(
    tmp_path: Path, repo_config_dir: Path, store: Any, *, tl_cfg: ToolLoopConfig | None = None
) -> ToolLoop:
    config = load_config(repo_config_dir)
    tools = build_tools(
        config, workspace=tmp_path, executor=_Executor(), rag_backend=InMemoryRagBackend()
    )
    kind_of = {name: tc.kind for name, tc in config.tools.items()}
    return ToolLoop(
        ToolDispatcher(tools, kind_of), AuditLog(), tl_cfg or ToolLoopConfig(), runs=store
    )


# --- Model ------------------------------------------------------------------


def test_malformed_ratio_without_steps_is_zero() -> None:
    """Brak tur nie może dzielić przez zero — przebieg odrzucony przez ROE nie ma ani jednej."""
    assert RunRecord(run_id="r", agent="a").malformed_ratio == 0.0


def test_metrics_count_correctly() -> None:
    record = RunRecord(
        run_id="r",
        agent="a",
        steps=[
            RunStep(index=0, kind=StepKind.MALFORMED),
            RunStep(index=1, kind=StepKind.ACTION, tool="shell", ok=True),
            RunStep(index=2, kind=StepKind.ACTION, tool="shell", ok=False),
            RunStep(index=3, kind=StepKind.ACTION, tool="web", ok=False, denied=True),
            RunStep(index=4, kind=StepKind.FINAL),
        ],
    )
    assert record.malformed_ratio == pytest.approx(0.2)
    # Odmowa bramki NIE jest wywołaniem narzędzia ani jego awarią — narzędzie nigdy nie
    # zostało dotknięte. Wliczanie jej sprawiałoby, że wskaźnik awaryjności rośnie wraz
    # ze skutecznością allowlisty, czyli metryka nagradzałaby słabsze zabezpieczenie.
    assert record.tool_calls == 2
    assert record.failed_tool_calls == 1
    assert record.denied_tool_calls == 1


# --- Magazyn ----------------------------------------------------------------


def test_null_store_is_default_and_writes_nothing(tmp_path: Path) -> None:
    NullRunStore().save(RunRecord(run_id="r", agent="a"))
    assert list(tmp_path.iterdir()) == []


def test_build_run_store_is_opt_in(tmp_path: Path) -> None:
    assert isinstance(build_run_store(enabled=False, path=tmp_path / "r.jsonl"), NullRunStore)
    assert isinstance(build_run_store(enabled=True, path=tmp_path / "r.jsonl"), JsonlRunStore)
    # Włączony pomiar bez ścieżki NIE jest błędem krytycznym — degradujemy, nie wywracamy.
    assert isinstance(build_run_store(enabled=True, path=None), NullRunStore)


def test_jsonl_store_appends_one_line_per_run(tmp_path: Path) -> None:
    store = JsonlRunStore(tmp_path / "podkatalog" / "runs.jsonl")
    store.save(RunRecord(run_id="r1", agent="a"))
    store.save(RunRecord(run_id="r2", agent="b"))
    lines = store.path.read_text(encoding="utf-8").strip().splitlines()
    assert [json.loads(x)["run_id"] for x in lines] == ["r1", "r2"]


def test_store_failure_never_propagates(tmp_path: Path) -> None:
    """Pomiar jakości nie może wywrócić pracy agenta — utrata pomiaru jest do przyjęcia."""
    kolizja = tmp_path / "plik"
    kolizja.write_text("nie katalog", encoding="utf-8")
    JsonlRunStore(kolizja / "runs.jsonl").save(RunRecord(run_id="r", agent="a"))


# --- Wpięcie w pętlę --------------------------------------------------------


def test_final_answer_records_run(tmp_path: Path, repo_config_dir: Path) -> None:
    store = _CollectingStore()
    loop = _loop(tmp_path, repo_config_dir, store)
    loop.run(
        _agent(["file_edit"]),
        "zadanie",
        router=_Router(["gotowe"]),
        budget=loop.new_budget(),
        run_id="abc",
    )
    (record,) = store.saved
    assert record.run_id == "abc"
    assert record.agent == "kopijnik"
    assert record.task_chars == len("zadanie")
    assert record.termination is Termination.FINAL
    assert [s.kind for s in record.steps] == [StepKind.FINAL]


def test_tool_call_and_final_are_both_recorded(tmp_path: Path, repo_config_dir: Path) -> None:
    store = _CollectingStore()
    loop = _loop(tmp_path, repo_config_dir, store)
    loop.run(
        _agent(["file_edit"]),
        "x",
        router=_Router([_action("file_edit", "write", {"path": "a.md", "content": "1"}), "gotowe"]),
        budget=loop.new_budget(),
    )
    (record,) = store.saved
    assert [s.kind for s in record.steps] == [StepKind.ACTION, StepKind.FINAL]
    assert record.steps[0].tool == "file_edit" and record.steps[0].ok is True
    assert record.tool_calls == 1 and record.failed_tool_calls == 0


def test_denied_tool_is_distinguished_from_failure(tmp_path: Path, repo_config_dir: Path) -> None:
    """Bez tego rozróżnienia nie da się zmierzyć skuteczności allowlisty agenta."""
    store = _CollectingStore()
    loop = _loop(tmp_path, repo_config_dir, store)
    loop.run(
        _agent(["file_edit"]),  # 'shell' POZA allowlistą agenta
        "x",
        router=_Router([_action("shell", "run", {"command": "ls"}), "gotowe"]),
        budget=loop.new_budget(),
    )
    (record,) = store.saved
    assert record.denied_tool_calls == 1
    assert record.steps[0].ok is False and record.steps[0].denied is True


def test_malformed_turns_are_measured(tmp_path: Path, repo_config_dir: Path) -> None:
    store = _CollectingStore()
    loop = _loop(tmp_path, repo_config_dir, store)
    loop.run(
        _agent(["file_edit"]),
        "x",
        router=_Router([f"{ACTION_OPEN}{{zły json{ACTION_CLOSE}", "gotowe"]),
        budget=loop.new_budget(),
    )
    (record,) = store.saved
    assert [s.kind for s in record.steps] == [StepKind.MALFORMED, StepKind.FINAL]
    assert record.malformed_ratio == pytest.approx(0.5)


def test_iteration_limit_is_recorded(tmp_path: Path, repo_config_dir: Path) -> None:
    store = _CollectingStore()
    loop = _loop(tmp_path, repo_config_dir, store)
    loop.run(
        _agent(["file_edit"], max_iterations=2),
        "x",
        router=_Router([f"{ACTION_OPEN}{{zły{ACTION_CLOSE}"]),
        budget=loop.new_budget(),
    )
    (record,) = store.saved
    assert record.termination is Termination.ITERATION_LIMIT
    assert len(record.steps) == 2


def test_budget_exhaustion_is_recorded(tmp_path: Path, repo_config_dir: Path) -> None:
    store = _CollectingStore()
    loop = _loop(tmp_path, repo_config_dir, store, tl_cfg=ToolLoopConfig(max_total_calls=1))
    loop.run(
        _agent(["file_edit"], max_iterations=8),
        "x",
        router=_Router(
            [
                _action("file_edit", "write", {"path": "a.md", "content": "1"}),
                _action("file_edit", "write", {"path": "b.md", "content": "2"}),
            ]
        ),
        budget=loop.new_budget(),
    )
    (record,) = store.saved
    assert record.termination is Termination.BUDGET


def test_roe_agent_records_refusal(tmp_path: Path, repo_config_dir: Path) -> None:
    store = _CollectingStore()
    loop = _loop(tmp_path, repo_config_dir, store)
    loop.run(
        _agent(["file_edit"], roe_required=True),
        "x",
        router=_Router(["gotowe"]),
        budget=loop.new_budget(),
    )
    (record,) = store.saved
    assert record.termination is Termination.ROE_REFUSED
    assert record.steps == []


def test_tokens_are_summed_and_survive_missing_usage(tmp_path: Path, repo_config_dir: Path) -> None:
    """Backend bez raportowania `usage` nie może wywrócić pomiaru — ma dawać zero."""
    store = _CollectingStore()
    loop = _loop(tmp_path, repo_config_dir, store)
    loop.run(_agent(["file_edit"]), "x", router=_Router(["gotowe"]), budget=loop.new_budget())
    assert store.saved[0].total_tokens == 0

    store2 = _CollectingStore()
    loop2 = _loop(tmp_path, repo_config_dir, store2)
    loop2.run(
        _agent(["file_edit"]),
        "x",
        router=_Router(["gotowe"], usage=Usage(total_tokens=17)),
        budget=loop2.new_budget(),
    )
    assert store2.saved[0].total_tokens == 17
    assert store2.saved[0].steps[0].total_tokens == 17


# --- Rekord orkiestracji ----------------------------------------------------


def test_plan_validity_counts_only_bad_steps() -> None:
    from husarz.runs import OrchestrationRecord

    # Mianownikiem jest liczba kroków PLANU, nie wszystkich delegacji: kroki z refleksji
    # powstają w innej fazie i zacierałyby trafność planisty na starcie.
    rec = OrchestrationRecord(
        run_id="r", plan_steps=4, plan_unknown_agent=1, delegations=6, skipped_unknown_agent=2
    )
    assert rec.plan_validity == pytest.approx(0.75)


def test_plan_validity_of_empty_plan_is_one() -> None:
    """Brak kroków to brak BŁĘDNYCH kroków — inaczej pusty plan wyglądałby na katastrofę."""
    from husarz.runs import OrchestrationRecord

    assert OrchestrationRecord(run_id="r").plan_validity == 1.0


def test_orchestration_record_has_no_content_fields() -> None:
    """Ta sama zasada co w RunRecord: metryki, nie treść — także dla planu i syntezy."""
    from dataclasses import fields

    from husarz.runs import OrchestrationRecord

    zakazane = {"task", "plan", "answer", "prompt", "observations", "content", "text"}
    assert {f.name for f in fields(OrchestrationRecord)} & zakazane == set()


def test_orchestrator_records_plan_quality(repo_config_dir: Path, tmp_path: Path) -> None:
    """Krok planu wskazujący NIEISTNIEJĄCEGO agenta musi być policzony jako wada planu.

    To nie jest przypadek teoretyczny — przy realnym modelu 7B planista pisał „Kanclerz"
    zamiast „kanclerz", a orkiestrator fail-closed pomijał taki krok. Bez pomiaru wyglądało
    to na krótszą odpowiedź, nie na wadę planowania.
    """
    from husarz.config import load_config
    from husarz.orchestrator import build_orchestrator
    from husarz.runs import OrchestrationRecord

    config = load_config(repo_config_dir)
    istniejacy = next(a for a in config.agents if a != "husarz")

    class _PlanRouter:
        """Zwraca plan z jednym agentem istniejącym i jednym widmowym."""

        def __init__(self) -> None:
            self._i = 0

        def complete(self, request: Any, **kwargs: Any) -> ChatResponse:
            self._i += 1
            if self._i == 1:
                # Plan jest JSON-em (patrz husarz.orchestrator.plan.parse_plan).
                plan = json.dumps(
                    {
                        "steps": [
                            {"agent": istniejacy, "task": "zrób A"},
                            {"agent": "Agent-Widmo", "task": "zrób B"},
                        ]
                    }
                )
                return ChatResponse(model="m", content=plan)
            return ChatResponse(model="m", content="gotowe")

    class _Store:
        def __init__(self) -> None:
            self.orch: list[OrchestrationRecord] = []

        def save(self, record: Any) -> None:
            if isinstance(record, OrchestrationRecord):
                self.orch.append(record)

    store = _Store()
    orch = build_orchestrator(
        config,
        _PlanRouter(),
        prompts_dir=repo_config_dir.parent / "prompts",
        runs=store,
    )
    orch.run("zadanie testowe", run_id="orch-1")

    (record,) = store.orch
    assert record.run_id == "orch-1"
    assert record.task_chars == len("zadanie testowe")
    assert record.skipped_unknown_agent >= 1, "krok z widmowym agentem musi być policzony"
    assert record.plan_validity < 1.0
