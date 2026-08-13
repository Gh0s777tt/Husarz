"""Testy ładowarki narzędzi (build_tools)."""

from __future__ import annotations

from pathlib import Path

import pytest

from husarz.config import load_config
from husarz.tools import (
    ExecResult,
    FileEditTool,
    InMemoryRagBackend,
    RagTool,
    SandboxSpec,
    ShellTool,
    ToolError,
    WebTool,
    build_tools,
)

pytestmark = pytest.mark.unit


class FakeExecutor:
    def run(self, spec: SandboxSpec) -> ExecResult:
        return ExecResult(exit_code=0, stdout="ok")


class FakeFetcher:
    def __call__(self, url: str, *, timeout: int, max_bytes: int) -> tuple[int, str]:
        return 200, "ok"


def test_build_tools_from_repo_config(repo_config_dir: Path, tmp_path: Path) -> None:
    config = load_config(repo_config_dir)
    tools = build_tools(
        config,
        workspace=tmp_path,
        executor=FakeExecutor(),
        fetcher=FakeFetcher(),
        rag_backend=InMemoryRagBackend(),
    )
    assert set(tools) == {"file_edit", "shell", "git", "run_tests", "web", "rag"}
    assert isinstance(tools["file_edit"], FileEditTool)
    assert isinstance(tools["shell"], ShellTool)
    assert isinstance(tools["web"], WebTool)
    assert isinstance(tools["rag"], RagTool)


def test_build_tools_skips_disabled(write_config, tmp_path: Path) -> None:
    models = "default: m1\nregistry:\n  m1:\n    backend: mock\n    model: x\n"
    tool_yaml = "name: rag\nkind: rag\nenabled: false\n"
    config_dir = write_config({"models.yaml": models, "tools/rag.yaml": tool_yaml})
    config = load_config(config_dir)
    assert build_tools(config, workspace=tmp_path) == {}


def test_build_tools_unknown_kind_raises(write_config, tmp_path: Path) -> None:
    models = "default: m1\nregistry:\n  m1:\n    backend: mock\n    model: x\n"
    config_dir = write_config(
        {"models.yaml": models, "tools/x.yaml": "name: teleporter\nkind: teleport\n"}
    )
    config = load_config(config_dir)
    with pytest.raises(ToolError, match="teleport"):
        build_tools(config, workspace=tmp_path, executor=FakeExecutor(), fetcher=FakeFetcher())
