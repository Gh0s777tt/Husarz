"""Narzędzie wtyczki (kind ``plugin``) — most do JEDNEGO konektora MCP w pętli agenta.

Wiąże się z konektorem z ``config/plugins/`` przez ``config.plugin`` (parytet 1:1 z ``web``:
allowlista L1 per-narzędzie). Dwie akcje:

- ``list`` — odkrywanie (``tools/list``), bramkowane tylko przez ``enabled`` (read-only),
- ``call`` — wywołanie (``tools/call``), deny-by-default (``allow_call`` + ``call_allowlist``).

Kontrakt „narzędzie NIGDY nie rzuca": błędy wtyczki (``PluginError`` i podklasy) oraz egress
(``EgressError``) degradują do ``ToolResult(ok=False)`` — pętla/orkiestracja nie pada. Wynik
jest NIEZAUFANY (nazwy/opisy zdalne, treść ``call``): pętla ogradza go jako DANE (``role='user'``),
a komunikaty błędów są generyczne (``_generic`` tłumi echo ``token_ref``).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import TYPE_CHECKING, Any

from husarz.fencing import truncate_utf8
from husarz.plugins.errors import PluginError, PluginSecretError
from husarz.plugins.models import RemoteTool
from husarz.router.egress import EgressError
from husarz.tools.base import ToolResult

if TYPE_CHECKING:
    from husarz.plugins.service import PluginService


def _generic(exc: Exception) -> str:
    """Generyczny komunikat błędu — NIE echuje ``token_ref`` ani wnętrza serwera."""
    if isinstance(exc, PluginSecretError):
        # Komunikat źródłowy może zawierać referencję sekretu (token_ref) — nie ujawniamy jej.
        return "Błąd konfiguracji sekretu wtyczki (sprawdź token_ref w config/plugins/)."
    return str(exc)


def _render_tools(tools: Iterable[RemoteTool]) -> str:
    """Renderuje listę zdalnych narzędzi (NIEZAUFANE nazwy/opisy) jako czysty tekst."""
    lines = [f"- {t.name}: {t.description}" if t.description else f"- {t.name}" for t in tools]
    return "\n".join(lines) if lines else "(serwer nie udostępnił żadnych narzędzi)"


class PluginTool:
    """Narzędzie ``plugin`` opakowujące ``PluginService`` dla jednego konektora."""

    def __init__(
        self,
        name: str,
        plugin_name: str,
        service: PluginService | None,
        *,
        max_output_bytes: int,
    ) -> None:
        self.name = name
        self._plugin = plugin_name
        self._service = service
        self._max_output_bytes = max_output_bytes

    def _cap(self, text: str) -> str:
        capped, _ = truncate_utf8(text, self._max_output_bytes)
        return capped

    def list(self) -> ToolResult:
        """Odkrywa narzędzia konektora (``tools/list``). Wynik NIEZAUFANY → dane w pętli."""
        if self._service is None:
            return ToolResult(self.name, ok=False, error="Usługa wtyczek niedostępna.")
        try:
            tools = self._service.discover(self._plugin)
        except (PluginError, EgressError) as exc:
            return ToolResult(self.name, ok=False, error=_generic(exc))
        return ToolResult(self.name, ok=True, output=self._cap(_render_tools(tools)))

    def call(self, remote_tool: str, arguments: Mapping[str, Any]) -> ToolResult:
        """Wywołuje zdalne narzędzie (``tools/call``) za bramami deny-by-default (w serwisie)."""
        if self._service is None:
            return ToolResult(self.name, ok=False, error="Usługa wtyczek niedostępna.")
        try:
            result = self._service.call(self._plugin, remote_tool, arguments)
        except (PluginError, EgressError) as exc:
            return ToolResult(self.name, ok=False, error=_generic(exc))
        if result.is_error:
            return ToolResult(
                self.name, ok=False, error="Zdalne narzędzie zgłosiło błąd (isError)."
            )
        return ToolResult(self.name, ok=True, output=self._cap(result.text))
