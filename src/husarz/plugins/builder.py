"""Budowa ``PluginService`` z konfiguracji (konektory + egress + dostawca sekretów)."""

from __future__ import annotations

from collections.abc import Mapping

from husarz.config.schema import PluginConfig, SecurityConfig
from husarz.config.secrets import SecretsProvider
from husarz.plugins.client import PluginTransport
from husarz.plugins.service import PluginService


def build_plugin_service(
    plugins: Mapping[str, PluginConfig],
    security: SecurityConfig,
    *,
    secrets: SecretsProvider | None = None,
    transport: PluginTransport | None = None,
) -> PluginService | None:
    """Buduje ``PluginService`` wg ``config.plugins`` i ``security.egress``.

    Zwraca ``None``, gdy ŻADNA wtyczka nie jest włączona (deny-by-default: API wtyczek
    odpowie wtedy 404). Konektory są statyczne — źródłem prawdy jest config.
    """
    if not any(plugin.enabled for plugin in plugins.values()):
        return None
    return PluginService(plugins, secrets=secrets, egress=security.egress, transport=transport)
