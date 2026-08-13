"""Narzędzia agentów — file_edit, shell (sandbox), git, run_tests, web, rag.

Każde narzędzie egzekwuje allowlisty i konfinację; polecenia biegną w sandboxie
bez sieci (o ile nie dozwolona). Executor/fetcher/backend są wstrzykiwalne —
testy nie wykonują połączeń sieciowych ani nie wymagają Dockera.
"""

from __future__ import annotations

from husarz.tools.base import Tool, ToolResult
from husarz.tools.errors import (
    CommandNotAllowedError,
    EgressNotAllowedError,
    PathNotAllowedError,
    SandboxError,
    ToolError,
    ToolNotAllowedError,
)
from husarz.tools.file_edit import FileEditTool
from husarz.tools.git import GitTool
from husarz.tools.loader import build_tools
from husarz.tools.rag import InMemoryRagBackend, RagBackend, RagTool
from husarz.tools.run_tests import RunTestsTool
from husarz.tools.sandbox import (
    DockerSandboxExecutor,
    ExecResult,
    SandboxExecutor,
    SandboxSpec,
    build_docker_argv,
    exec_to_result,
    spec_from_config,
)
from husarz.tools.shell import ShellTool
from husarz.tools.web import Fetcher, HttpxFetcher, WebTool
from husarz.tools.workspace import glob_match, resolve_within_workspace

__all__ = [
    "CommandNotAllowedError",
    "DockerSandboxExecutor",
    "EgressNotAllowedError",
    "ExecResult",
    "Fetcher",
    "FileEditTool",
    "GitTool",
    "HttpxFetcher",
    "InMemoryRagBackend",
    "PathNotAllowedError",
    "RagBackend",
    "RagTool",
    "RunTestsTool",
    "SandboxError",
    "SandboxExecutor",
    "SandboxSpec",
    "ShellTool",
    "Tool",
    "ToolError",
    "ToolNotAllowedError",
    "ToolResult",
    "WebTool",
    "build_docker_argv",
    "build_tools",
    "exec_to_result",
    "glob_match",
    "resolve_within_workspace",
    "spec_from_config",
]
