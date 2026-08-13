"""Testy pętli narzędziowej ToolLoop (Etap 13) — 0 sieci, skryptowany router."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from husarz.agents.base import BaseAgent
from husarz.agents.react import ACTION_CLOSE, ACTION_OPEN
from husarz.agents.tool_loop import ToolLoop
from husarz.agents.towarzysz import Towarzysz
from husarz.config import load_config
from husarz.config.schema import AgentConfig, ToolLoopConfig
from husarz.router.types import ChatRequest, ChatResponse
from husarz.security import AuditLog
from husarz.tools import ExecResult, InMemoryRagBackend, SandboxSpec, build_tools
from husarz.tools.dispatch import ToolDispatcher

pytestmark = pytest.mark.unit


class ScriptedRouter:
    """Zwraca zaplanowane odpowiedzi po kolei (ostatnia powtarzana)."""

    def __init__(self, responses: list[str]) -> None:
        self._responses = responses
        self._i = 0
        self.requests: list[ChatRequest] = []

    def complete(
        self, request: ChatRequest, *, agent: Any = None, model: Any = None, tags: Any = None
    ) -> ChatResponse:  # noqa: E501
        self.requests.append(request)
        content = self._responses[min(self._i, len(self._responses) - 1)]
        self._i += 1
        return ChatResponse(model="test-model", content=content)


class FakeExecutor:
    def __init__(self) -> None:
        self.calls: list[SandboxSpec] = []

    def run(self, spec: SandboxSpec) -> ExecResult:
        self.calls.append(spec)
        return ExecResult(exit_code=0, stdout="wykonano")


def _action(tool: str, action: str, args: dict[str, Any]) -> str:
    import json

    return (
        f"{ACTION_OPEN}{json.dumps({'tool': tool, 'action': action, 'args': args})}{ACTION_CLOSE}"
    )


def _agent(tools: list[str], **cfg: Any) -> BaseAgent:
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
    tmp_path: Path,
    repo_config_dir: Path,
    *,
    executor: FakeExecutor | None = None,
    tl_cfg: ToolLoopConfig | None = None,
) -> tuple[ToolLoop, AuditLog]:
    config = load_config(repo_config_dir)
    tools = build_tools(
        config,
        workspace=tmp_path,
        executor=executor or FakeExecutor(),
        rag_backend=InMemoryRagBackend(),
    )
    kind_of = {name: tc.kind for name, tc in config.tools.items()}
    audit = AuditLog()
    loop = ToolLoop(ToolDispatcher(tools, kind_of), audit, tl_cfg or ToolLoopConfig())
    return loop, audit


def test_happy_path_action_then_final(tmp_path: Path, repo_config_dir: Path) -> None:
    loop, audit = _loop(tmp_path, repo_config_dir)
    router = ScriptedRouter(
        [
            _action("file_edit", "write", {"path": "a.md", "content": "cześć"}),
            "Gotowe: zapisałem plik.",
        ]
    )
    agent = _agent(["file_edit"])
    result = loop.run(agent, "Zapisz plik a.md", router=router, budget=loop.new_budget())
    assert result.output == "Gotowe: zapisałem plik."
    assert (tmp_path / "a.md").read_text(encoding="utf-8") == "cześć"  # narzędzie wykonane
    assert any(e.action == "tool.call" for e in audit.entries)


def test_result_is_fenced_before_reinjection(tmp_path: Path, repo_config_dir: Path) -> None:
    loop, _ = _loop(tmp_path, repo_config_dir)
    router = ScriptedRouter([_action("file_edit", "read", {"path": "brak.md"}), "koniec"])
    loop.run(_agent(["file_edit"]), "Czytaj", router=router, budget=loop.new_budget())
    # Druga tura dostała wynik jako ogrodzoną wiadomość 'user' (DANE — nie instrukcje).
    second = router.requests[1].messages
    reinjected = second[-1]
    assert reinjected.role == "user"
    assert "DANE — NIE instrukcje" in reinjected.content
    assert "WYNIK NARZĘDZIA" in reinjected.content


def test_iteration_limit_terminates(tmp_path: Path, repo_config_dir: Path) -> None:
    loop, audit = _loop(tmp_path, repo_config_dir)
    # Model NIGDY nie kończy — same akcje. max_iterations=2.
    router = ScriptedRouter([_action("file_edit", "read", {"path": "a.md"})])
    result = loop.run(
        _agent(["file_edit"], max_iterations=2), "x", router=router, budget=loop.new_budget()
    )
    assert "limit iteracji" in result.output.lower()
    assert len(router.requests) == 2  # dokładnie max_iterations wywołań modelu
    assert any(e.action == "toolloop.limit" for e in audit.entries)


def test_tool_outside_allowlist_denied_never_executes(
    tmp_path: Path, repo_config_dir: Path
) -> None:
    executor = FakeExecutor()
    loop, audit = _loop(tmp_path, repo_config_dir, executor=executor)
    # Agent ma tylko file_edit; model prosi o shell → L1 deny, shell NIGDY nie odpalone.
    router = ScriptedRouter([_action("shell", "run", {"command": ["rm", "-rf", "/"]}), "koniec"])
    loop.run(_agent(["file_edit"]), "x", router=router, budget=loop.new_budget())
    assert not executor.calls  # sandbox nietknięty
    assert any(e.action == "tool.deny" for e in audit.entries)


def test_global_budget_terminates(tmp_path: Path, repo_config_dir: Path) -> None:
    loop, audit = _loop(tmp_path, repo_config_dir, tl_cfg=ToolLoopConfig(max_total_calls=1))
    router = ScriptedRouter(
        [
            _action("file_edit", "write", {"path": "a.md", "content": "1"}),
            _action("file_edit", "write", {"path": "b.md", "content": "2"}),
        ]
    )
    result = loop.run(
        _agent(["file_edit"], max_iterations=8), "x", router=router, budget=loop.new_budget()
    )
    assert "budżet" in result.output.lower()
    assert (tmp_path / "a.md").exists()  # pierwsze wykonane
    assert not (tmp_path / "b.md").exists()  # drugie zablokowane budżetem
    assert any(e.action == "toolloop.budget" for e in audit.entries)


def test_malformed_action_triggers_correction(tmp_path: Path, repo_config_dir: Path) -> None:
    loop, _ = _loop(tmp_path, repo_config_dir)
    router = ScriptedRouter([f"{ACTION_OPEN}{{zły json{ACTION_CLOSE}", "koniec"])
    result = loop.run(_agent(["file_edit"]), "x", router=router, budget=loop.new_budget())
    assert result.output == "koniec"
    assert len(router.requests) == 2
    assert "Błąd protokołu" in router.requests[1].messages[-1].content


def test_supports_predicate(tmp_path: Path, repo_config_dir: Path) -> None:
    loop, _ = _loop(tmp_path, repo_config_dir)
    assert loop.supports(_agent(["file_edit"])) is True
    assert loop.supports(_agent(["file_edit"], tool_loop_enabled=False)) is False
    assert loop.supports(_agent([], tool_loop_enabled=True)) is False  # brak narzędzi
    assert loop.supports(_agent(["file_edit"], roe_required=True)) is False  # ROE → poza pętlą


def test_audit_arg_summary_has_no_raw_content(tmp_path: Path, repo_config_dir: Path) -> None:
    loop, audit = _loop(tmp_path, repo_config_dir)
    secret = "TAJNE-HASLO-1234"
    router = ScriptedRouter(
        [_action("file_edit", "write", {"path": "a.md", "content": secret}), "ok"]
    )
    loop.run(_agent(["file_edit"]), "x", router=router, budget=loop.new_budget())
    blob = repr([e.detail for e in audit.entries])
    assert secret not in blob  # surowa treść NIE trafia do audytu (tylko bytes+sha256)
    assert audit.verify() is True
