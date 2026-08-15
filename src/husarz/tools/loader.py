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
from typing import TYPE_CHECKING

from husarz.config.schema import (
    FileEditSettings,
    GitToolSettings,
    HusarzConfig,
    PluginToolSettings,
    RagBackendConfig,
    RunTestsSettings,
    WebToolSettings,
)
from husarz.config.secrets import NullSecretsProvider, SecretsProvider
from husarz.ssrf import HostResolver
from husarz.tools.base import Tool
from husarz.tools.errors import ToolError
from husarz.tools.file_edit import FileEditTool
from husarz.tools.git import GitTool
from husarz.tools.rag import RagBackend, RagTool
from husarz.tools.registry import BuildContext, ToolProviderRegistry
from husarz.tools.run_tests import RunTestsTool
from husarz.tools.sandbox import DockerSandboxExecutor, SandboxExecutor
from husarz.tools.shell import ShellTool
from husarz.tools.web import Fetcher, HttpxFetcher, WebTool

if TYPE_CHECKING:
    from husarz.plugins.service import PluginService

# Domyślny cap tekstu wyniku narzędzia plugin (nadpisywalny per-narzędzie: config.max_output_bytes).
_DEFAULT_PLUGIN_OUTPUT_BYTES = 100_000


# ---------------------------------------------------------------------------
# Buildery wbudowanych rodzajów narzędzi (1:1 z dawnym dispatch if/elif).
# Każdy: BuildContext -> Tool. Zarejestrowane w ``default_registry``.
# ---------------------------------------------------------------------------


def _build_file_edit(ctx: BuildContext) -> Tool:
    settings = ctx.tool_config.settings_as(FileEditSettings)
    return FileEditTool(
        ctx.workspace_path,
        deny_globs=list(settings.deny_globs),
        max_bytes=settings.max_file_bytes,
    )


def _build_shell(ctx: BuildContext) -> Tool:
    return ShellTool(
        ctx.executor,
        command_allowlist=ctx.tool_config.allowlist,
        sandbox=ctx.security.sandbox,
        workspace_host_path=ctx.workspace_host,
    )


def _build_git(ctx: BuildContext) -> Tool:
    settings = ctx.tool_config.settings_as(GitToolSettings)
    return GitTool(
        ctx.executor,
        subcommand_allowlist=ctx.tool_config.allowlist,
        sandbox=ctx.security.sandbox,
        allow_push=settings.allow_push,
        workspace_host_path=ctx.workspace_host,
    )


def _build_run_tests(ctx: BuildContext) -> Tool:
    settings = ctx.tool_config.settings_as(RunTestsSettings)
    return RunTestsTool(
        ctx.executor,
        command=shlex.split(settings.command),
        sandbox=ctx.security.sandbox,
        workspace_host_path=ctx.workspace_host,
    )


def _build_web(ctx: BuildContext) -> Tool:
    settings = ctx.tool_config.settings_as(WebToolSettings)
    return WebTool(
        ctx.fetcher,
        domain_allowlist=ctx.tool_config.allowlist,
        egress=ctx.security.egress,
        max_bytes=settings.max_bytes,
        timeout=settings.timeout_seconds,
        resolve=ctx.resolve,
    )


def _build_rag(ctx: BuildContext) -> Tool:
    # Jawnie wstrzyknięty backend (testy/back-compat) ma pierwszeństwo.
    if ctx.rag_backend is not None:
        return RagTool(ctx.rag_backend, top_k=ctx.tool_config.settings_as(RagBackendConfig).top_k)
    # Inaczej buduj backend z konfiguracji narzędzia (memory/embedding) — import leniwy,
    # by rdzeń nie ciągnął zależności pamięci (embedder/store) bez potrzeby.
    from husarz.memory import build_rag_backend  # noqa: PLC0415

    # Konfiguracja jest już ZWALIDOWANA przy starcie (ToolConfig._validate_settings) —
    # tu tylko sięgamy po typowany obiekt, bez ponownego parsowania.
    rag_config = ctx.tool_config.settings_as(RagBackendConfig)
    backend = build_rag_backend(
        rag_config, ctx.security, data_dir=ctx.data_dir, secrets=ctx.secrets, resolve=ctx.resolve
    )
    return RagTool(backend, top_k=rag_config.top_k)


def _build_plugin(ctx: BuildContext) -> Tool:
    # Import leniwy (spójnie z _build_rag) — brak cyklu, plugins nie importuje tools.
    from husarz.tools.plugin import PluginTool  # noqa: PLC0415

    settings = ctx.tool_config.settings_as(PluginToolSettings)
    return PluginTool(
        ctx.name,
        settings.plugin.strip(),
        ctx.plugin_service,
        max_output_bytes=settings.max_output_bytes,
    )


def default_registry() -> ToolProviderRegistry:
    """Buduje rejestr z 7 wbudowanymi rodzajami narzędzi (świeża instancja)."""
    registry = ToolProviderRegistry()
    registry.register("file_edit", _build_file_edit)
    registry.register("shell", _build_shell)
    registry.register("git", _build_git)
    registry.register("run_tests", _build_run_tests)
    registry.register("web", _build_web)
    registry.register("rag", _build_rag)
    registry.register("plugin", _build_plugin)
    return registry


def build_tools(
    config: HusarzConfig,
    *,
    workspace: str | Path,
    executor: SandboxExecutor | None = None,
    fetcher: Fetcher | None = None,
    rag_backend: RagBackend | None = None,
    secrets: SecretsProvider | None = None,
    data_dir: str | Path = "./data",
    plugin_service: PluginService | None = None,
    registry: ToolProviderRegistry | None = None,
    resolve: HostResolver | None = None,
) -> dict[str, Tool]:
    """Buduje mapę ``nazwa -> narzędzie`` z konfiguracji.

    Narzędzia wyłączone (``enabled: false``) są pomijane. Nieznany ``kind``
    kończy się ``ToolError``. ``registry`` (opcjonalny) pozwala rozszerzyć lub
    podmienić zestaw rodzajów (domyślnie ``default_registry``). ``rag_backend``
    jawnie wstrzyknięty ma pierwszeństwo; gdy ``None`` — narzędzie rag buduje backend
    z własnej konfiguracji (``memory``/``embedding``). ``resolve`` (opcjonalny) to
    resolver DNS dla pinowania IP narzędzia ``web`` — w testach fałszywy (bez sieci).
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
                data_dir=Path(data_dir),
                plugin_service=plugin_service,
                resolve=resolve,
            )
        )
    return tools
