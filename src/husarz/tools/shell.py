"""Narzędzie shell — wykonanie polecenia w sandboxie, tylko z allowlisty.

Polecenie musi zaczynać się od binarki z allowlisty; całość biegnie w sandboxie
(domyślnie bez sieci, z limitami). Żadnej powłoki — argumenty przekazywane jako lista.
"""

from __future__ import annotations

from husarz.config.schema import SandboxConfig
from husarz.tools.base import ToolResult
from husarz.tools.sandbox import SandboxExecutor, exec_to_result, spec_from_config


class ShellTool:
    """Uruchamia dozwolone polecenia w izolowanym sandboxie."""

    name = "shell"

    def __init__(
        self,
        executor: SandboxExecutor,
        *,
        command_allowlist: list[str],
        sandbox: SandboxConfig,
        workspace_host_path: str | None = None,
    ) -> None:
        self._executor = executor
        self._allow = set(command_allowlist)
        self._sandbox = sandbox
        self._workspace_host_path = workspace_host_path

    def run(self, command: list[str]) -> ToolResult:
        if not command:
            return ToolResult(self.name, ok=False, error="Puste polecenie.")
        binary = command[0]
        if binary not in self._allow:
            return ToolResult(self.name, ok=False, error=f"Polecenie '{binary}' spoza allowlisty.")
        spec = spec_from_config(
            command, self._sandbox, workspace_host_path=self._workspace_host_path
        )
        return exec_to_result(self.name, self._executor.run(spec))
