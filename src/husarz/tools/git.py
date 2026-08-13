"""Narzędzie git — operacje w workspace, tylko dozwolone podkomendy.

``push`` jest blokowany, dopóki ``allow_push`` nie jest jawnie włączone (push
wymaga sieci i osobnej zgody). Wykonanie biegnie w sandboxie jak shell.
"""

from __future__ import annotations

from husarz.config.schema import SandboxConfig
from husarz.tools.base import ToolResult
from husarz.tools.sandbox import SandboxExecutor, SandboxSpec, exec_to_result


class GitTool:
    """Operacje git ograniczone allowlistą podkomend."""

    name = "git"

    def __init__(
        self,
        executor: SandboxExecutor,
        *,
        subcommand_allowlist: list[str],
        sandbox: SandboxConfig,
        allow_push: bool = False,
        workspace_host_path: str | None = None,
    ) -> None:
        self._executor = executor
        self._allow = set(subcommand_allowlist)
        self._sandbox = sandbox
        self._allow_push = allow_push
        self._workspace_host_path = workspace_host_path

    def run(self, args: list[str]) -> ToolResult:
        if not args:
            return ToolResult(self.name, ok=False, error="Brak podkomendy git.")
        subcommand = args[0]
        if subcommand not in self._allow:
            return ToolResult(
                self.name, ok=False, error=f"Podkomenda git '{subcommand}' spoza allowlisty."
            )
        if subcommand == "push" and not self._allow_push:
            return ToolResult(
                self.name, ok=False, error="git push jest wyłączony (allow_push=false)."
            )
        spec = SandboxSpec(
            command=["git", *args],
            network=self._sandbox.network,
            cpu_limit=self._sandbox.cpu_limit,
            memory_limit=self._sandbox.memory_limit,
            timeout_seconds=self._sandbox.timeout_seconds,
            image=self._sandbox.image,
            runtime_class=self._sandbox.runtime_class,
            workspace_host_path=self._workspace_host_path,
        )
        return exec_to_result(self.name, self._executor.run(spec))
