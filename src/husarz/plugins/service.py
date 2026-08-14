"""PluginService — fasada wtyczek: konektory z konfiguracji + odkrywanie narzędzi.

Konektory są STATYCZNE (źródłem prawdy jest ``config/plugins/*.yaml``) — brak
mutowalnego magazynu (inaczej niż GitService). Token rozwiązywany jest z referencji
(``token_ref``) przez dostawcę sekretów DOPIERO przy operacji (nie przechowywany).
Egress endpointu sprawdzany jest w ``build_connector`` (deny-all). Transport
wstrzykiwalny (testy bez sieci). Odkrywanie (``tools/list``) oraz wywołanie
(``tools/call``) — to drugie za bramami ``allow_call``/``call_allowlist`` (ADR-0019).
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from husarz.config.schema import EgressConfig, PluginConfig
from husarz.config.secrets import NullSecretsProvider, SecretsProvider
from husarz.plugins.client import HostResolver, McpClient, PluginTransport, build_connector
from husarz.plugins.errors import (
    PluginArgsError,
    PluginCallDeniedError,
    PluginDisabledError,
    PluginNotFoundError,
    PluginSecretError,
)
from husarz.plugins.models import RemoteCallResult, RemoteTool


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

    def call(self, name: str, tool: str, arguments: Mapping[str, Any]) -> RemoteCallResult:
        """Wywołuje zdalne narzędzie wtyczki (``tools/call``) za bramami deny-by-default.

        Bramy PRZED egress (transport NIE jest dotykany przy odmowie):

        1. ``allow_call=false`` → ``PluginCallDeniedError`` (master-switch).
        2. ``tool`` spoza ``call_allowlist`` → ``PluginCallDeniedError`` (jawna enumeracja).
        3. zły typ / ``params`` (name+arguments) > ``max_call_bytes`` → ``PluginArgsError``.

        Dalej ``_client`` sprawdza ``enabled``, rozwiązuje token z referencji leniwie i buduje
        konektor z RE-WALIDACJĄ egress/SSRF PRZED połączeniem. ``arguments`` lecą VERBATIM
        (żadne ``env:/file:/...`` NIE są rozwiązywane — sekret modelu nie jest eksfiltrowany).

        Raises:
            PluginNotFoundError / PluginDisabledError / PluginSecretError / PluginAuthError /
            PluginCallDeniedError / PluginArgsError / EgressError / PluginError.
        """
        plugin = self.get(name)  # PluginNotFoundError
        if not plugin.allow_call:
            raise PluginCallDeniedError(
                f"Wtyczka '{name}' nie zezwala na wywołania (allow_call=false)."
            )
        if tool not in plugin.call_allowlist:
            raise PluginCallDeniedError(
                f"Narzędzie '{tool}' spoza call_allowlist wtyczki '{name}'."
            )
        _check_args_size(name, tool, arguments, plugin.max_call_bytes)
        return self._client(name).call_tool(tool, arguments)


def _check_args_size(name: str, tool: str, arguments: Mapping[str, Any], max_bytes: int) -> None:
    """Waliduje typ i rozmiar CAŁYCH ``params`` (name+arguments) PRZED egress (fail-closed).

    Cap dotyczy zserializowanego żądania (nie tylko ``arguments``), więc patologicznie długa
    ``name`` też jest ograniczona. Zły typ / niese­rializowalne / za duże → ``PluginArgsError``.
    """
    if not isinstance(arguments, Mapping):
        raise PluginArgsError(f"Wtyczka '{name}': arguments muszą być obiektem (mapą).")
    try:
        blob = json.dumps({"name": tool, "arguments": dict(arguments)}, ensure_ascii=False)
    except (TypeError, ValueError) as exc:
        raise PluginArgsError(
            f"Wtyczka '{name}': arguments nie są serializowalne do JSON."
        ) from exc
    size = len(blob.encode("utf-8"))
    if size > max_bytes:
        raise PluginArgsError(
            f"Wtyczka '{name}': żądanie ({size} B) przekracza max_call_bytes ({max_bytes} B)."
        )
