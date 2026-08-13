"""Ładowarka narzędzi z konfiguracji.

Buduje instancje narzędzi z ``config.tools`` (``config/tools/*.yaml``), honorując
``enabled`` i allowlisty. Dispatch po ``kind`` przechodzi przez wstrzykiwalny
``ToolProviderRegistry`` (patrz ``husarz.tools.registry``) — nowy rodzaj narzędzia
= nowa funkcja-builder + jedna linia ``register`` w ``default_registry``, bez zmian
w ``build_tools``. Executor sandboxa, fetcher HTTP i backend RAG są wstrzykiwalne —
w produkcji domyślne (Docker/httpx/in-memory), w testach mocki.
"""

from __future__ import annotations

import shlex
from pathlib import Path
from typing import Any

from husarz.config.schema import HusarzConfig
from husarz.config.secrets import NullSecretsProvider, SecretsProvider
from husarz.tools.base import Tool
from husarz.tools.errors import ToolError
from husarz.tools.file_edit import DEFAULT_MAX_BYTES, FileEditTool
from husarz.tools.git import GitTool
from husarz.tools.rag import DEFAULT_TOP_K, RagBackend, RagTool
from husarz.tools.registry import BuildContext, ToolProviderRegistry
from husarz.tools.run_tests import RunTestsTool
from husarz.tools.sandbox import DockerSandboxExecutor, SandboxExecutor
from husarz.tools.shell import ShellTool
from husarz.tools.web import DEFAULT_MAX_BYTES as WEB_MAX_BYTES
from husarz.tools.web import DEFAULT_TIMEOUT, Fetcher, HttpxFetcher, WebTool


def _int_setting(settings: dict[str, Any], key: str, default: int) -> int:
    """Zwraca wartość int z ustawień; brak klucza LUB jawny ``null`` -> ``default``."""
    value = settings.get(key)
    return default if value is None else int(value)


# ---------------------------------------------------------------------------
# Buildery wbudowanych rodzajów narzędzi (1:1 z dawnym dispatch if/elif).
# Każdy: BuildContext -> Tool. Zarejestrowane w ``default_registry``.
# ---------------------------------------------------------------------------


def _build_file_edit(ctx: BuildContext) -> Tool:
    settings = ctx.tool_config.config
    return FileEditTool(
        ctx.workspace_path,
        deny_globs=list(settings.get("deny_globs") or []),
        max_bytes=_int_setting(settings, "max_file_bytes", DEFAULT_MAX_BYTES),
    )


def _build_shell(ctx: BuildContext) -> Tool:
    return ShellTool(
        ctx.executor,
        command_allowlist=ctx.tool_config.allowlist,
        sandbox=ctx.security.sandbox,
        workspace_host_path=ctx.workspace_host,
    )


def _build_git(ctx: BuildContext) -> Tool:
    settings = ctx.tool_config.config
    return GitTool(
        ctx.executor,
        subcommand_allowlist=ctx.tool_config.allowlist,
        sandbox=ctx.security.sandbox,
        allow_push=bool(settings.get("allow_push", False)),
        workspace_host_path=ctx.workspace_host,
    )


def _build_run_tests(ctx: BuildContext) -> Tool:
    settings = ctx.tool_config.config
    return RunTestsTool(
        ctx.executor,
        command=shlex.split(str(settings.get("command") or "pytest -q")),
        sandbox=ctx.security.sandbox,
        workspace_host_path=ctx.workspace_host,
    )


def _build_web(ctx: BuildContext) -> Tool:
    settings = ctx.tool_config.config
    return WebTool(
        ctx.fetcher,
        domain_allowlist=ctx.tool_config.allowlist,
        egress=ctx.security.egress,
        max_bytes=_int_setting(settings, "max_bytes", WEB_MAX_BYTES),
        timeout=_int_setting(settings, "timeout_seconds", DEFAULT_TIMEOUT),
    )


def _build_rag(ctx: BuildContext) -> Tool:
    # Jawnie wstrzyknięty backend (testy/back-compat) ma pierwszeństwo.
    if ctx.rag_backend is not None:
        top_k = _int_setting(ctx.tool_config.config, "top_k", DEFAULT_TOP_K)
        return RagTool(ctx.rag_backend, top_k=top_k)
    # Inaczej buduj backend z konfiguracji narzędzia (memory/embedding) — import leniwy,
    # by rdzeń nie ciągnął zależności pamięci (embedder/store) bez potrzeby.
    from husarz.config.schema import RagBackendConfig  # noqa: PLC0415
    from husarz.memory import build_rag_backend  # noqa: PLC0415

    # Klucze o wartości null traktujemy jak brak (jak dawne _int_setting) → domyślne z schematu.
    settings = {key: value for key, value in ctx.tool_config.config.items() if value is not None}
    rag_config = RagBackendConfig(**settings)
    backend = build_rag_backend(rag_config, ctx.security.egress, secrets=ctx.secrets)
    return RagTool(backend, top_k=rag_config.top_k)


def default_registry() -> ToolProviderRegistry:
    """Buduje rejestr z 6 wbudowanymi rodzajami narzędzi (świeża instancja)."""
    registry = ToolProviderRegistry()
    registry.register("file_edit", _build_file_edit)
    registry.register("shell", _build_shell)
    registry.register("git", _build_git)
    registry.register("run_tests", _build_run_tests)
    registry.register("web", _build_web)
    registry.register("rag", _build_rag)
    return registry


def build_tools(
    config: HusarzConfig,
    *,
    workspace: str | Path,
    executor: SandboxExecutor | None = None,
    fetcher: Fetcher | None = None,
    rag_backend: RagBackend | None = None,
    secrets: SecretsProvider | None = None,
    registry: ToolProviderRegistry | None = None,
) -> dict[str, Tool]:
    """Buduje mapę ``nazwa -> narzędzie`` z konfiguracji.

    Narzędzia wyłączone (``enabled: false``) są pomijane. Nieznany ``kind``
    kończy się ``ToolError``. ``registry`` (opcjonalny) pozwala rozszerzyć lub
    podmienić zestaw rodzajów (domyślnie ``default_registry``). ``rag_backend``
    jawnie wstrzyknięty ma pierwszeństwo; gdy ``None`` — narzędzie rag buduje backend
    z własnej konfiguracji (``memory``/``embedding``).
    """
    workspace_path = Path(workspace)
    workspace_host = str(workspace_path.resolve())
    active_registry = registry if registry is not None else default_registry()
    active_executor: SandboxExecutor = executor or DockerSandboxExecutor()
    active_fetcher: Fetcher = fetcher or HttpxFetcher()
    active_secrets: SecretsProvider = secrets if secrets is not None else NullSecretsProvider()

    tools: dict[str, Tool] = {}
    for name, tool_config in config.tools.items():
        if not tool_config.enabled:
            continue
        builder = active_registry.get(tool_config.kind)
        if builder is None:
            raise ToolError(f"Nieznany rodzaj narzędzia '{tool_config.kind}' (narzędzie '{name}').")
        tools[name] = builder(
            BuildContext(
                name=name,
                tool_config=tool_config,
                workspace_path=workspace_path,
                workspace_host=workspace_host,
                security=config.security,
                executor=active_executor,
                fetcher=active_fetcher,
                rag_backend=rag_backend,
                secrets=active_secrets,
            )
        )
    return tools
