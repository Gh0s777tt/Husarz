"""System wtyczek Husarza — konektory MCP (data-driven, suwerennie).

Rozszerzalność ZEWNĘTRZNA rdzenia: zamiast ładować obcy kod (RCE/łańcuch dostaw),
Husarz łączy się z lokalnym serwerem narzędzi MCP przez WSTRZYKIWALNY transport HTTP
JSON-RPC. Token to referencja do sekretu (nie plaintext), endpoint przez bramkę egress
(deny-all, anty-SSRF), a odpowiedź jest traktowana jako NIEZAUFANA. Odkrywanie narzędzi
(``tools/list``) oraz wywołanie (``tools/call``) — to drugie deny-by-default przez
``allow_call``/``call_allowlist`` i pętlę agenta (ADR-0015, ADR-0019).
"""

from __future__ import annotations

from husarz.plugins.builder import build_plugin_service
from husarz.plugins.client import (
    HttpxPluginTransport,
    McpClient,
    PluginTransport,
    build_connector,
)
from husarz.plugins.errors import (
    PluginArgsError,
    PluginAuthError,
    PluginCallDeniedError,
    PluginDisabledError,
    PluginError,
    PluginNotFoundError,
    PluginSecretError,
    PluginTransportError,
)
from husarz.plugins.models import RemoteCallResult, RemoteTool
from husarz.plugins.service import PluginService

__all__ = [
    "HttpxPluginTransport",
    "McpClient",
    "PluginArgsError",
    "PluginAuthError",
    "PluginCallDeniedError",
    "PluginDisabledError",
    "PluginError",
    "PluginNotFoundError",
    "PluginSecretError",
    "PluginService",
    "PluginTransport",
    "PluginTransportError",
    "RemoteCallResult",
    "RemoteTool",
    "build_connector",
    "build_plugin_service",
]
