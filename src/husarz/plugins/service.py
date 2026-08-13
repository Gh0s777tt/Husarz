"""PluginService — fasada wtyczek: konektory z konfiguracji + odkrywanie narzędzi.

Konektory są STATYCZNE (źródłem prawdy jest ``config/plugins/*.yaml``) — brak
mutowalnego magazynu (inaczej niż GitService). Token rozwiązywany jest z referencji
(``token_ref``) przez dostawcę sekretów DOPIERO przy operacji (nie przechowywany).
Egress endpointu sprawdzany jest w ``build_connector`` (deny-all). Transport
wstrzykiwalny (testy bez sieci). MVP: odkrywanie (``tools/list``).
"""

from __future__ import annotations

from collections.abc import Mapping

from husarz.config.schema import EgressConfig, PluginConfig
from husarz.config.secrets import NullSecretsProvider, SecretsProvider
from husarz.plugins.client import HostResolver, McpClient, PluginTransport, build_connector
from husarz.plugins.errors import (
    PluginDisabledError,
    PluginNotFoundError,
    PluginSecretError,
)
from husarz.plugins.models import RemoteTool


class PluginService:
    """Zarządza konektorami wtyczek (z configu) i odkrywa ich narzędzia na żądanie."""

    def __init__(
        self,
        plugins: Mapping[str, PluginConfig],
        *,
        secrets: SecretsProvider | None = None,
        egress: EgressConfig | None = None,
        transport: PluginTransport | None = None,
        resolve: HostResolver | None = None,
    ) -> None:
        self._plugins: dict[str, PluginConfig] = dict(plugins)
        self._secrets = secrets if secrets is not None else NullSecretsProvider()
        self._egress = egress if egress is not None else EgressConfig()
        self._transport = transport
        self._resolve = resolve

    def list_plugins(self) -> list[PluginConfig]:
        """Zwraca wszystkie skonfigurowane wtyczki (włączone i wyłączone)."""
        return list(self._plugins.values())

    def get(self, name: str) -> PluginConfig:
        """Zwraca wtyczkę. Rzuca ``PluginNotFoundError``, gdy nie istnieje."""
        plugin = self._plugins.get(name)
        if plugin is None:
            raise PluginNotFoundError(f"Nieznana wtyczka: '{name}'.")
        return plugin

    def _client(self, name: str) -> McpClient:
        """Buduje klienta MCP dla wtyczki (rozwiązuje token leniwie, sprawdza egress)."""
        plugin = self.get(name)
        if not plugin.enabled:
            raise PluginDisabledError(f"Wtyczka '{name}' jest wyłączona.")
        token = ""
        if plugin.token_ref:
            resolved = self._secrets.resolve(plugin.token_ref)
            if not resolved or not resolved.strip():
                # Lokalna błędna konfiguracja sekretu (NIE odmowa serwera) — osobny typ,
                # by API nie obwiniało zdalnego serwera (fail-closed, bez wyjścia na sieć).
                raise PluginSecretError(
                    f"Nie udało się rozwiązać referencji sekretu wtyczki '{name}' "
                    f"(token_ref='{plugin.token_ref}')."
                )
            token = resolved.strip()
        return build_connector(
            plugin, token, self._egress, transport=self._transport, resolve=self._resolve
        )

    def discover(self, name: str) -> list[RemoteTool]:
        """Odkrywa narzędzia zdalnego serwera wtyczki (``tools/list``).

        Raises:
            PluginNotFoundError: nieznana wtyczka.
            PluginDisabledError: wtyczka wyłączona.
            PluginSecretError: nie udało się rozwiązać ``token_ref`` (lokalna konfiguracja).
            PluginAuthError: zdalna odmowa autoryzacji serwera (401/403).
            EgressError: host endpointu niedozwolony (deny-all/SSRF/rebinding).
        """
        return self._client(name).list_tools()
