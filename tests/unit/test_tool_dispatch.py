"""Testy jednolitego dispatchu narzędzi (Etap 13): mapowanie akcji, walidacja args, manual."""

from __future__ import annotations

from pathlib import Path

import pytest

from husarz.config import load_config
from husarz.tools import ExecResult, InMemoryRagBackend, SandboxSpec, build_tools
from husarz.tools.dispatch import ActionRegistry, ToolDispatcher, default_action_registry

pytestmark = pytest.mark.unit


class FakeExecutor:
    def __init__(self) -> None:
        self.calls: list[SandboxSpec] = []

    def run(self, spec: SandboxSpec) -> ExecResult:
        self.calls.append(spec)
        return ExecResult(exit_code=0, stdout="ok")


class FakeFetcher:
    def __init__(self) -> None:
        self.urls: list[str] = []

    def __call__(self, url: str, *, timeout: int, max_bytes: int) -> tuple[int, str]:
        self.urls.append(url)
        return 200, "TREŚĆ"


def _dispatcher(tmp_path: Path, repo_config_dir: Path, **kw: object) -> ToolDispatcher:
    overrides = (
        {"security": {"egress": {"default_policy": "allow"}}} if kw.get("allow_egress") else None
    )
    config = load_config(repo_config_dir, runtime_overrides=overrides)
    tools = build_tools(
        config,
        workspace=tmp_path,
        executor=kw.get("executor") or FakeExecutor(),  # type: ignore[arg-type]
        fetcher=kw.get("fetcher") or FakeFetcher(),  # type: ignore[arg-type]
        rag_backend=kw.get("rag") or InMemoryRagBackend(),  # type: ignore[arg-type]
    )
    kind_of = {name: tc.kind for name, tc in config.tools.items()}
    descriptions = {name: tc.description for name, tc in config.tools.items()}
    return ToolDispatcher(tools, kind_of, descriptions=descriptions)


def test_file_edit_read_write_roundtrip(tmp_path: Path, repo_config_dir: Path) -> None:
    disp = _dispatcher(tmp_path, repo_config_dir)
    w = disp.dispatch("file_edit", "write", {"path": "notes/a.md", "content": "cześć"})
    assert w.ok
    r = disp.dispatch("file_edit", "read", {"path": "notes/a.md"})
    assert r.ok and "cześć" in r.output


def test_shell_run_dispatches_to_executor(tmp_path: Path, repo_config_dir: Path) -> None:
    executor = FakeExecutor()
    disp = _dispatcher(tmp_path, repo_config_dir, executor=executor)
    res = disp.dispatch("shell", "run", {"command": ["ls", "-la"]})
    assert res.ok
    assert executor.calls  # narzędzie faktycznie odpalone w sandboxie


def test_web_fetch_dispatches_to_fetcher(tmp_path: Path, repo_config_dir: Path) -> None:
    fetcher = FakeFetcher()
    disp = _dispatcher(tmp_path, repo_config_dir, fetcher=fetcher, allow_egress=True)
    res = disp.dispatch("web", "fetch", {"url": "https://docs.python.org/3/"})
    assert res.ok and fetcher.urls == ["https://docs.python.org/3/"]


def test_rag_add_then_search(tmp_path: Path, repo_config_dir: Path) -> None:
    backend = InMemoryRagBackend()
    disp = _dispatcher(tmp_path, repo_config_dir, rag=backend)
    assert disp.dispatch("rag", "add", {"text": "hetman husarz chorągiew"}).ok
    res = disp.dispatch("rag", "search", {"query": "husarz"})
    assert res.ok


# --- Walidacja args: zły kształt → ok=False, BEZ wyjątku i BEZ efektu -------------------


def test_bad_args_shell_command_not_list(tmp_path: Path, repo_config_dir: Path) -> None:
    executor = FakeExecutor()
    disp = _dispatcher(tmp_path, repo_config_dir, executor=executor)
    res = disp.dispatch("shell", "run", {"command": "ls -la"})  # str zamiast list
    assert res.ok is False
    assert not executor.calls  # narzędzie NIE wywołane


def test_missing_arg_file_read(tmp_path: Path, repo_config_dir: Path) -> None:
    disp = _dispatcher(tmp_path, repo_config_dir)
    res = disp.dispatch("file_edit", "read", {})
    assert res.ok is False


def test_rag_add_oversize_rejected(tmp_path: Path, repo_config_dir: Path) -> None:
    backend = InMemoryRagBackend()
    disp = _dispatcher(tmp_path, repo_config_dir, rag=backend)
    res = disp.dispatch("rag", "add", {"text": "x" * 200_001})
    assert res.ok is False


def test_unknown_tool_and_action(tmp_path: Path, repo_config_dir: Path) -> None:
    disp = _dispatcher(tmp_path, repo_config_dir)
    assert disp.dispatch("teleporter", "run", {}).ok is False
    assert disp.dispatch("web", "delete", {"url": "x"}).ok is False  # akcja spoza rejestru


def test_kind_of_honored_not_instance_name(tmp_path: Path, repo_config_dir: Path) -> None:
    # Nazwa narzędzia w configu może różnić się od kind — dispatch używa kind_of, nie name.
    config = load_config(repo_config_dir)
    tools = build_tools(config, workspace=tmp_path, executor=FakeExecutor(), fetcher=FakeFetcher())
    # podmieniamy klucz: 'moj_shell' -> instancja shell, kind_of mówi 'shell'
    tools2 = {"moj_shell": tools["shell"]}
    disp = ToolDispatcher(tools2, {"moj_shell": "shell"})
    assert disp.dispatch("moj_shell", "run", {"command": ["ls"]}).ok


def test_action_registry_duplicate_raises() -> None:
    from husarz.tools.dispatch import ActionSpec
    from husarz.tools.errors import ToolError

    reg = default_action_registry()
    with pytest.raises(ToolError, match="już zarejestrowana"):
        reg.register("web", ActionSpec("fetch", lambda t, a: t.name, "dup"))  # type: ignore[arg-type,return-value]


def test_manual_lists_only_allowlisted(tmp_path: Path, repo_config_dir: Path) -> None:
    disp = _dispatcher(tmp_path, repo_config_dir)
    manual = disp.manual(["file_edit", "web"])
    assert "file_edit" in manual and "web" in manual
    assert "shell" not in manual  # spoza allowlisty
    assert 'akcja "read"' in manual and 'akcja "fetch"' in manual


def test_empty_registry_dispatch_is_denied(tmp_path: Path, repo_config_dir: Path) -> None:
    config = load_config(repo_config_dir)
    tools = build_tools(config, workspace=tmp_path, executor=FakeExecutor(), fetcher=FakeFetcher())
    kind_of = {name: tc.kind for name, tc in config.tools.items()}
    disp = ToolDispatcher(tools, kind_of, registry=ActionRegistry())  # pusty rejestr
    assert disp.dispatch("web", "fetch", {"url": "https://x"}).ok is False


def test_rag_add_cap_is_configurable(tmp_path: Path, repo_config_dir: Path) -> None:
    # Cap rag.add nie jest zaszyty — niższy limit z konfiguracji jest honorowany.
    config = load_config(repo_config_dir)
    tools = build_tools(
        config, workspace=tmp_path, executor=FakeExecutor(), rag_backend=InMemoryRagBackend()
    )
    kind_of = {name: tc.kind for name, tc in config.tools.items()}
    disp = ToolDispatcher(tools, kind_of, max_rag_add_bytes=10)
    assert disp.dispatch("rag", "add", {"text": "x" * 11}).ok is False  # ponad limit 10
    assert disp.dispatch("rag", "add", {"text": "krótki"}).ok is True  # w limicie


def test_mismatched_kind_returns_error_not_raise(tmp_path: Path, repo_config_dir: Path) -> None:
    # Niespójny kind_of (WebTool zadeklarowany jako 'shell') → cast trafia w brakującą
    # metodę .run; dispatch MUSI zwrócić ok=False, nie rzucić AttributeError.
    config = load_config(repo_config_dir)
    tools = build_tools(config, workspace=tmp_path, executor=FakeExecutor(), fetcher=FakeFetcher())
    disp = ToolDispatcher({"web": tools["web"]}, {"web": "shell"})
    res = disp.dispatch("web", "run", {"command": ["ls"]})
    assert res.ok is False
    assert "rodzaj" in res.error.lower()
