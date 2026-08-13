"""Ładowarka narzędzi z konfiguracji.

Buduje instancje narzędzi z ``config.tools`` (``config/tools/*.yaml``), honorując
``enabled`` i allowlisty. Executor sandboxa, fetcher HTTP i backend RAG są
wstrzykiwalne — w produkcji domyślne (Docker/httpx/in-memory), w testach mocki.
"""

from __future__ import annotations

import shlex
from pathlib import Path
from typing import Any

from husarz.config.schema import HusarzConfig
from husarz.tools.base import Tool
from husarz.tools.errors import ToolError
from husarz.tools.file_edit import DEFAULT_MAX_BYTES, FileEditTool
from husarz.tools.git import GitTool
from husarz.tools.rag import DEFAULT_TOP_K, InMemoryRagBackend, RagBackend, RagTool
from husarz.tools.run_tests import RunTestsTool
from husarz.tools.sandbox import DockerSandboxExecutor, SandboxExecutor
from husarz.tools.shell import ShellTool
from husarz.tools.web import DEFAULT_MAX_BYTES as WEB_MAX_BYTES
from husarz.tools.web import DEFAULT_TIMEOUT, Fetcher, HttpxFetcher, WebTool


def _int_setting(settings: dict[str, Any], key: str, default: int) -> int:
    """Zwraca wartość int z ustawień; brak klucza LUB jawny ``null`` -> ``default``."""
    value = settings.get(key)
    return default if value is None else int(value)


def build_tools(
    config: HusarzConfig,
    *,
    workspace: str | Path,
    executor: SandboxExecutor | None = None,
    fetcher: Fetcher | None = None,
    rag_backend: RagBackend | None = None,
) -> dict[str, Tool]:
    """Buduje mapę ``nazwa -> narzędzie`` z konfiguracji.

    Narzędzia wyłączone (``enabled: false``) są pomijane. Nieznany ``kind``
    kończy się ``ToolError``.
    """
    workspace_path = Path(workspace)
    workspace_host = str(workspace_path.resolve())
    security = config.security
    active_executor: SandboxExecutor = executor or DockerSandboxExecutor()
    active_fetcher: Fetcher = fetcher or HttpxFetcher()
    active_backend: RagBackend = rag_backend or InMemoryRagBackend()

    tools: dict[str, Tool] = {}
    for name, tool_config in config.tools.items():
        if not tool_config.enabled:
            continue
        kind = tool_config.kind
        settings = tool_config.config

        if kind == "file_edit":
            tools[name] = FileEditTool(
                workspace_path,
                deny_globs=list(settings.get("deny_globs") or []),
                max_bytes=_int_setting(settings, "max_file_bytes", DEFAULT_MAX_BYTES),
            )
        elif kind == "shell":
            tools[name] = ShellTool(
                active_executor,
                command_allowlist=tool_config.allowlist,
                sandbox=security.sandbox,
                workspace_host_path=workspace_host,
            )
        elif kind == "git":
            tools[name] = GitTool(
                active_executor,
                subcommand_allowlist=tool_config.allowlist,
                sandbox=security.sandbox,
                allow_push=bool(settings.get("allow_push", False)),
                workspace_host_path=workspace_host,
            )
        elif kind == "run_tests":
            tools[name] = RunTestsTool(
                active_executor,
                command=shlex.split(str(settings.get("command") or "pytest -q")),
                sandbox=security.sandbox,
                workspace_host_path=workspace_host,
            )
        elif kind == "web":
            tools[name] = WebTool(
                active_fetcher,
                domain_allowlist=tool_config.allowlist,
                egress=security.egress,
                max_bytes=_int_setting(settings, "max_bytes", WEB_MAX_BYTES),
                timeout=_int_setting(settings, "timeout_seconds", DEFAULT_TIMEOUT),
            )
        elif kind == "rag":
            tools[name] = RagTool(
                active_backend, top_k=_int_setting(settings, "top_k", DEFAULT_TOP_K)
            )
        else:
            raise ToolError(f"Nieznany rodzaj narzędzia '{kind}' (narzędzie '{name}').")
    return tools
