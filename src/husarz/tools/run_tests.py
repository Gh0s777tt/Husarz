"""Narzędzie run_tests — uruchomienie zestawu testów w sandboxie."""

from __future__ import annotations

from husarz.config.schema import SandboxConfig
from husarz.tools.base import ToolResult
from husarz.tools.sandbox import SandboxExecutor, SandboxSpec, exec_to_result


class RunTestsTool:
    """Uruchamia skonfigurowane polecenie testów (np. ``pytest -q``) w sandboxie."""

    name = "run_tests"

    def __init__(
        self,
        executor: SandboxExecutor,
        *,
        command: list[str],
        sandbox: SandboxConfig,
        workspace_host_path: str | None = None,
    ) -> None:
        self._executor = executor
        self._command = command
        self._sandbox = sandbox
        self._workspace_host_path = workspace_host_path

    def run(self, extra_args: list[str] | None = None) -> ToolResult:
        command = [*self._command, *(extra_args or [])]
        spec = SandboxSpec(
            command=command,
            network=self._sandbox.network,
            cpu_limit=self._sandbox.cpu_limit,
            memory_limit=self._sandbox.memory_limit,
            timeout_seconds=self._sandbox.timeout_seconds,
            image=self._sandbox.image,
            runtime_class=self._sandbox.runtime_class,
            workspace_host_path=self._workspace_host_path,
        )
        return exec_to_result(self.name, self._executor.run(spec))
