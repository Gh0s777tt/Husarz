"""Testy sandboxa: budowa argv Dockera oraz narzędzia shell/git/run_tests."""

from __future__ import annotations

import pytest

from husarz.config.schema import SandboxConfig
from husarz.tools import (
    ExecResult,
    GitTool,
    RunTestsTool,
    SandboxError,
    SandboxSpec,
    ShellTool,
    build_docker_argv,
    exec_to_result,
)

pytestmark = pytest.mark.unit


class FakeExecutor:
    """Executor testowy — zapisuje specyfikacje, zwraca ustalony wynik."""

    def __init__(self, result: ExecResult | None = None) -> None:
        self.specs: list[SandboxSpec] = []
        self._result = result or ExecResult(exit_code=0, stdout="ok")

    def run(self, spec: SandboxSpec) -> ExecResult:
        self.specs.append(spec)
        return self._result


def _sandbox() -> SandboxConfig:
    return SandboxConfig(image="husarz-sandbox:latest")  # network False domyślnie


# --- build_docker_argv -----------------------------------------------------


def test_argv_isolation_defaults() -> None:
    argv = build_docker_argv(
        SandboxSpec(command=["python", "-c", "print(1)"], image="img", workspace_host_path="/host")
    )
    assert argv[:3] == ["docker", "run", "--rm"]
    assert argv[argv.index("--network") + 1] == "none"  # brak sieci
    assert "--cap-drop" in argv and "ALL" in argv
    assert "no-new-privileges" in argv
    assert "/host:/workspace" in argv
    assert argv[-4:] == ["img", "python", "-c", "print(1)"]  # obraz, potem polecenie


def test_argv_network_and_gvisor() -> None:
    argv = build_docker_argv(
        SandboxSpec(command=["ls"], image="img", network=True, runtime_class="runsc")
    )
    assert argv[argv.index("--network") + 1] == "bridge"
    assert argv[argv.index("--runtime") + 1] == "runsc"


def test_argv_requires_image() -> None:
    with pytest.raises(SandboxError):
        build_docker_argv(SandboxSpec(command=["ls"]))


def test_exec_to_result_maps_exit_code() -> None:
    assert exec_to_result("shell", ExecResult(exit_code=0, stdout="out")).ok is True
    failed = exec_to_result("shell", ExecResult(exit_code=1, stderr="err"))
    assert failed.ok is False
    assert failed.error == "err"


# --- ShellTool -------------------------------------------------------------


def test_shell_blocks_unlisted_binary() -> None:
    executor = FakeExecutor()
    tool = ShellTool(executor, command_allowlist=["python"], sandbox=_sandbox())
    result = tool.run(["rm", "-rf", "/"])
    assert result.ok is False
    assert "allowlisty" in result.error
    assert executor.specs == []  # executor NIE został wywołany


def test_shell_runs_allowed_binary_without_network() -> None:
    executor = FakeExecutor()
    tool = ShellTool(
        executor, command_allowlist=["python"], sandbox=_sandbox(), workspace_host_path="/ws"
    )
    result = tool.run(["python", "-c", "1"])
    assert result.ok
    spec = executor.specs[0]
    assert spec.network is False
    assert spec.command == ["python", "-c", "1"]


def test_shell_empty_command() -> None:
    assert (
        ShellTool(FakeExecutor(), command_allowlist=["ls"], sandbox=_sandbox()).run([]).ok is False
    )


# --- GitTool ---------------------------------------------------------------


def test_git_allows_listed_subcommand() -> None:
    executor = FakeExecutor()
    tool = GitTool(executor, subcommand_allowlist=["status"], sandbox=_sandbox())
    assert tool.run(["status"]).ok
    assert executor.specs[0].command == ["git", "status"]


def test_git_blocks_unlisted_subcommand() -> None:
    tool = GitTool(FakeExecutor(), subcommand_allowlist=["status"], sandbox=_sandbox())
    assert tool.run(["clone", "url"]).ok is False


def test_git_push_blocked_by_default() -> None:
    tool = GitTool(FakeExecutor(), subcommand_allowlist=["push"], sandbox=_sandbox())
    result = tool.run(["push", "origin", "main"])
    assert result.ok is False
    assert "push" in result.error.lower()


def test_git_push_allowed_when_enabled() -> None:
    executor = FakeExecutor()
    tool = GitTool(executor, subcommand_allowlist=["push"], sandbox=_sandbox(), allow_push=True)
    assert tool.run(["push"]).ok
    assert executor.specs[0].command == ["git", "push"]


# --- RunTestsTool ----------------------------------------------------------


def test_run_tests_uses_configured_command() -> None:
    executor = FakeExecutor()
    tool = RunTestsTool(executor, command=["pytest", "-q"], sandbox=_sandbox())
    assert tool.run().ok
    assert executor.specs[0].command == ["pytest", "-q"]
