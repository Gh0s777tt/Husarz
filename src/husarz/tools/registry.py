"""Rejestr providerów narzędzi (open/closed) — zastępuje twardy dispatch po ``kind``.

Zamiast łańcucha ``if/elif`` w ładowarce, każdy rodzaj narzędzia (``kind``) ma
zarejestrowany **builder**: funkcję ``BuildContext -> Tool``. Nowy rodzaj = nowa
funkcja + jedna linia ``register`` w ``default_registry`` — BEZ zmian w rdzeniu
dispatchu (zasada „zero hardcode"). Rejestr jest budowany świeżo (brak globalnego
mutowalnego singletona), więc testy i re-importy nie mogą po cichu przesłonić
wbudowanego rodzaju.

Rejestr obsługuje wyłącznie providerów *first-party* (wbudowane rodzaje). ŚWIADOMIE
NIE ładuje zewnętrznych modułów Pythona (entry_points/importlib) — import obcego
kodu = wykonanie obcego kodu (RCE/supply-chain), sprzeczne z suwerennością i pakietem
frozen (PyInstaller). Rozszerzalność ZEWNĘTRZNĄ realizuje data-driven konektor MCP
(patrz ``husarz.plugins``), nie wtyczki jako kod.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from husarz.config.schema import SecurityConfig, ToolConfig
from husarz.config.secrets import SecretsProvider
from husarz.ssrf import HostResolver
from husarz.tools.base import Tool
from husarz.tools.errors import ToolError
from husarz.tools.rag import RagBackend
from husarz.tools.sandbox import SandboxExecutor
from husarz.tools.web import Fetcher

if TYPE_CHECKING:
    from husarz.plugins.service import PluginService


@dataclass(slots=True, frozen=True)
class BuildContext:
    """Komplet zależności do zbudowania jednego narzędzia z konfiguracji.

    Przekazywany do buildera zarejestrowanego pod danym ``kind``. Zależności
    (executor sandboxa, fetcher HTTP, backend RAG, dostawca sekretów) są współdzielone
    między narzędziami i wstrzykiwalne — w testach mockowane, bez sieci/dockera.

    ``rag_backend`` jawnie wstrzyknięty (nie ``None``) ma pierwszeństwo (back-compat/testy);
    gdy ``None``, narzędzie rag buduje backend z własnej konfiguracji (``memory``/``embedding``).
    """

    name: str
    tool_config: ToolConfig
    workspace_path: Path
    workspace_host: str
    security: SecurityConfig
    executor: SandboxExecutor
    fetcher: Fetcher
    rag_backend: RagBackend | None
    secrets: SecretsProvider
    data_dir: Path  # katalog trwałych danych (np. magazyn pamięci sqlite)
    # Usługa wtyczek MCP (dla kind=plugin). None → narzędzie plugin degraduje do ok=False.
    # TYPE_CHECKING-only import: plugins NIE importuje tools (brak cyklu). Ostatnie pole
    # z domyślną (back-compat wywołań build_tools/BuildContext bez plugin_service).
    plugin_service: PluginService | None = None
    # Resolver DNS dla pinowania IP (kind=web). None → stdlib ``default_resolve``.
    # Wstrzykiwalny szew: testy klasyfikują hosty bez odpytywania sieci (ADR-0020).
    resolve: HostResolver | None = None


# Builder: z kontekstu buduje gotowe narzędzie.
ToolBuilder = Callable[[BuildContext], Tool]


class ToolProviderRegistry:
    """Mapa ``kind -> builder`` z jawną rejestracją (jedyny punkt rozszerzeń)."""

    def __init__(self) -> None:
        self._providers: dict[str, ToolBuilder] = {}

    def register(self, kind: str, builder: ToolBuilder) -> None:
        """Rejestruje builder dla rodzaju ``kind``.

        Raises:
            ToolError: gdy ``kind`` jest już zarejestrowany (blokuje ciche
                przejęcie wbudowanego rodzaju — determinizm).
        """
        if kind in self._providers:
            raise ToolError(f"Rodzaj narzędzia '{kind}' jest już zarejestrowany.")
        self._providers[kind] = builder

    def get(self, kind: str) -> ToolBuilder | None:
        """Zwraca builder dla ``kind`` lub ``None``, gdy nieznany."""
        return self._providers.get(kind)

    def known_kinds(self) -> frozenset[str]:
        """Zbiór zarejestrowanych rodzajów (do diagnostyki/testów)."""
        return frozenset(self._providers)
