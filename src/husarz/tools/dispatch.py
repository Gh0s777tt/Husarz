"""Jednolity dispatch heterogenicznych metod narzędzi + schematy dla modelu.

Model prosi o ``(tool, action, args)``; ``ToolDispatcher`` tłumaczy to na wywołanie
KONKRETNEJ publicznej metody narzędzia przez **jawną tabelę akcji per KIND** — bez
``getattr`` na danych od modelu (to foot-gun: pozwoliłoby sięgnąć metod prywatnych).
Złe argumenty / nieznana akcja / nieznane narzędzie → ``ToolResult(ok=False)``, NIGDY
wyjątek i NIGDY efekt uboczny. ``ToolDispatcher.manual`` buduje z tej samej tabeli
deterministyczny „man" — jedno źródło prawdy: dispatch ↔ schemat (brak dryfu).

Nowy rodzaj narzędzia = builder (patrz ``tools.registry``) + wpisy ``ActionSpec`` w jednym
miejscu (``default_action_registry``), bez zmian w rdzeniu dispatchu (open/closed).
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, cast

from husarz.memory.errors import MemoryError_
from husarz.plugins.errors import PluginError
from husarz.router.egress import EgressError
from husarz.tools.base import Tool, ToolResult
from husarz.tools.errors import ToolError
from husarz.tools.file_edit import FileEditTool
from husarz.tools.git import GitTool
from husarz.tools.plugin import PluginTool
from husarz.tools.rag import RagTool
from husarz.tools.run_tests import RunTestsTool
from husarz.tools.shell import ShellTool
from husarz.tools.web import WebTool

# Domyślny (kodowy) cap tekstu dodawanego do RAG — nadpisywalny przez
# security.tool_loop.max_rag_add_bytes (zero-hardcode: defaults(kod) -> config -> ENV).
_DEFAULT_MAX_RAG_ADD_BYTES = 100_000

# Inwoker: (instancja narzędzia, args) -> ToolResult. Zna sygnaturę metody danego kind.
ToolInvoker = Callable[[Tool, dict[str, Any]], ToolResult]


@dataclass(slots=True, frozen=True)
class ActionSpec:
    """Opis pojedynczej akcji narzędzia: nazwa, inwoker i schemat parametrów (do manuala)."""

    action: str
    invoker: ToolInvoker
    summary: str
    params: dict[str, str] = field(default_factory=dict)  # nazwa -> opis (typ/rola)


class ActionRegistry:
    """Mapa ``kind -> {action -> ActionSpec}`` z jawną rejestracją (open/closed)."""

    def __init__(self) -> None:
        self._by_kind: dict[str, dict[str, ActionSpec]] = {}

    def register(self, kind: str, spec: ActionSpec) -> None:
        """Rejestruje akcję dla rodzaju ``kind`` (duplikat → ``ToolError``)."""
        actions = self._by_kind.setdefault(kind, {})
        if spec.action in actions:
            raise ToolError(f"Akcja '{spec.action}' rodzaju '{kind}' jest już zarejestrowana.")
        actions[spec.action] = spec

    def get(self, kind: str, action: str) -> ActionSpec | None:
        """Zwraca ``ActionSpec`` dla ``(kind, action)`` lub ``None``."""
        return self._by_kind.get(kind, {}).get(action)

    def actions_for(self, kind: str) -> dict[str, ActionSpec]:
        """Zwraca akcje dostępne dla rodzaju ``kind`` (mapa nazwa → spec)."""
        return dict(self._by_kind.get(kind, {}))


# --- Walidacja argumentów (bez wyjątku — zły kształt → ToolResult(ok=False)) ----------


def _err(tool: str, message: str) -> ToolResult:
    return ToolResult(tool=tool, ok=False, error=message)


def _get_str(args: dict[str, Any], key: str) -> str | None:
    value = args.get(key)
    return value if isinstance(value, str) else None


def _get_str_list(args: dict[str, Any], key: str) -> list[str] | None:
    value = args.get(key)
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return list(value)
    return None


# --- Inwokery wbudowanych rodzajów (1:1 z publicznymi metodami narzędzi) ---------------


def _inv_file_read(tool: Tool, args: dict[str, Any]) -> ToolResult:
    path = _get_str(args, "path")
    if path is None:
        return _err(tool.name, "file_edit.read wymaga argumentu 'path' (tekst).")
    return cast(FileEditTool, tool).read(path)


def _inv_file_write(tool: Tool, args: dict[str, Any]) -> ToolResult:
    path = _get_str(args, "path")
    content = _get_str(args, "content")
    if path is None or content is None:
        return _err(tool.name, "file_edit.write wymaga 'path' (tekst) i 'content' (tekst).")
    return cast(FileEditTool, tool).write(path, content)


def _inv_shell_run(tool: Tool, args: dict[str, Any]) -> ToolResult:
    command = _get_str_list(args, "command")
    if command is None:
        return _err(tool.name, 'shell.run wymaga \'command\' (lista tekstów, np. ["ls","-la"]).')
    return cast(ShellTool, tool).run(command)


def _inv_git_run(tool: Tool, args: dict[str, Any]) -> ToolResult:
    git_args = _get_str_list(args, "args")
    if git_args is None:
        return _err(tool.name, "git.run wymaga 'args' (lista tekstów, np. [\"status\"]).")
    return cast(GitTool, tool).run(git_args)


def _inv_run_tests(tool: Tool, args: dict[str, Any]) -> ToolResult:
    extra = args.get("extra_args")
    if extra is None:
        return cast(RunTestsTool, tool).run(None)
    extra_list = _get_str_list(args, "extra_args")
    if extra_list is None:
        return _err(tool.name, "run_tests.run: 'extra_args' musi być listą tekstów lub pominięte.")
    return cast(RunTestsTool, tool).run(extra_list)


def _inv_web_fetch(tool: Tool, args: dict[str, Any]) -> ToolResult:
    url = _get_str(args, "url")
    if url is None:
        return _err(tool.name, "web.fetch wymaga 'url' (tekst).")
    return cast(WebTool, tool).fetch(url)


def _make_rag_add(max_bytes: int) -> ToolInvoker:
    """Buduje inwoker ``rag.add`` z konfigurowalnym capem rozmiaru tekstu."""

    def _inv_rag_add(tool: Tool, args: dict[str, Any]) -> ToolResult:
        text = _get_str(args, "text")
        if text is None:
            return _err(tool.name, "rag.add wymaga 'text' (tekst).")
        if len(text.encode("utf-8")) > max_bytes:
            return _err(tool.name, f"rag.add: tekst przekracza limit ({max_bytes} B).")
        metadata = args.get("metadata")
        if metadata is not None and not isinstance(metadata, dict):
            return _err(tool.name, "rag.add: 'metadata' musi być obiektem (mapą) lub pominięte.")
        return cast(RagTool, tool).add(text, metadata)

    return _inv_rag_add


def _inv_rag_search(tool: Tool, args: dict[str, Any]) -> ToolResult:
    query = _get_str(args, "query")
    if query is None:
        return _err(tool.name, "rag.search wymaga 'query' (tekst).")
    return cast(RagTool, tool).search(query)


def _inv_plugin_list(tool: Tool, args: dict[str, Any]) -> ToolResult:
    # 'list' nie przyjmuje argumentów (odkrywanie) — ewentualne args są ignorowane.
    return cast(PluginTool, tool).list()


def _inv_plugin_call(tool: Tool, args: dict[str, Any]) -> ToolResult:
    name = _get_str(args, "name")
    if name is None:
        return _err(tool.name, "plugin.call wymaga 'name' (tekst — nazwa zdalnego narzędzia).")
    arguments = args.get("arguments")
    if arguments is None:
        arguments = {}
    elif not isinstance(arguments, dict):
        return _err(tool.name, "plugin.call: 'arguments' musi być obiektem (mapą) lub pominięte.")
    return cast(PluginTool, tool).call(name, arguments)


def default_action_registry(
    *, max_rag_add_bytes: int = _DEFAULT_MAX_RAG_ADD_BYTES
) -> ActionRegistry:
    """Rejestr akcji dla 7 wbudowanych rodzajów narzędzi (świeża instancja)."""
    registry = ActionRegistry()
    registry.register(
        "file_edit",
        ActionSpec("read", _inv_file_read, "Czyta plik z workspace.", {"path": "ścieżka (str)"}),
    )
    registry.register(
        "file_edit",
        ActionSpec(
            "write",
            _inv_file_write,
            "Zapisuje plik w workspace.",
            {"path": "ścieżka (str)", "content": "treść (str)"},
        ),
    )
    registry.register(
        "shell",
        ActionSpec(
            "run", _inv_shell_run, "Uruchamia komendę w sandboxie.", {"command": "argv (list[str])"}
        ),
    )
    registry.register(
        "git",
        ActionSpec(
            "run", _inv_git_run, "Uruchamia git w sandboxie.", {"args": "argv git (list[str])"}
        ),
    )
    registry.register(
        "run_tests",
        ActionSpec(
            "run",
            _inv_run_tests,
            "Uruchamia testy w sandboxie.",
            {"extra_args": "dodatkowe argumenty (list[str], opcjonalne)"},
        ),
    )
    registry.register(
        "web",
        ActionSpec(
            "fetch", _inv_web_fetch, "Pobiera URL (allowlista+egress).", {"url": "adres (str)"}
        ),
    )
    registry.register(
        "rag",
        ActionSpec(
            "add",
            _make_rag_add(max_rag_add_bytes),
            "Dodaje tekst do pamięci RAG.",
            {"text": "treść (str)", "metadata": "mapa (opcjonalna)"},
        ),
    )
    registry.register(
        "rag",
        ActionSpec(
            "search", _inv_rag_search, "Wyszukuje w pamięci RAG.", {"query": "zapytanie (str)"}
        ),
    )
    registry.register(
        "plugin",
        ActionSpec(
            "list",
            _inv_plugin_list,
            "Odkrywa narzędzia serwera MCP wtyczki (tools/list).",
            {},
        ),
    )
    registry.register(
        "plugin",
        ActionSpec(
            "call",
            _inv_plugin_call,
            "Wywołuje zdalne narzędzie MCP wtyczki (tools/call; wymaga allow_call+call_allowlist).",
            {
                "name": "nazwa zdalnego narzędzia (str)",
                "arguments": "argumenty zdalnego narzędzia (mapa, opcjonalne)",
            },
        ),
    )
    return registry


class ToolDispatcher:
    """Tłumaczy ``(tool, action, args)`` na wywołanie metody narzędzia (jawna tabela)."""

    def __init__(
        self,
        tools: dict[str, Tool],
        kind_of: dict[str, str],
        *,
        descriptions: dict[str, str] | None = None,
        registry: ActionRegistry | None = None,
        max_rag_add_bytes: int = _DEFAULT_MAX_RAG_ADD_BYTES,
    ) -> None:
        self._tools = tools
        self._kind_of = kind_of  # nazwa narzędzia (config) -> kind (metoda ≠ nazwa)
        self._descriptions = descriptions or {}
        self._registry = (
            registry
            if registry is not None
            else default_action_registry(max_rag_add_bytes=max_rag_add_bytes)
        )

    def dispatch(self, tool: str, action: str, args: dict[str, Any]) -> ToolResult:
        """Wywołuje akcję narzędzia. Nieznane tool/action/args → ``ToolResult(ok=False)``."""
        instance = self._tools.get(tool)
        if instance is None:
            return _err(tool, f"Nieznane narzędzie '{tool}'.")
        kind = self._kind_of.get(tool)
        spec = self._registry.get(kind, action) if kind is not None else None
        if spec is None:
            return _err(tool, f"Narzędzie '{tool}' nie obsługuje akcji '{action}'.")
        try:
            return spec.invoker(instance, args)
        except AttributeError:
            # Kontrakt „nigdy nie rzuca": niespójny kind_of (instancja innego rodzaju niż
            # deklarowany kind) → cast trafia w brakującą metodę. Zwracamy błąd, nie wyjątek.
            return _err(tool, f"Narzędzie '{tool}' nie pasuje do rodzaju '{kind}'.")
        except (MemoryError_, EgressError, PluginError) as exc:
            # Awaria backendu (embedder RAG, egress, wtyczka MCP) degraduje się do wyniku,
            # a NIE wywala pętli/orkiestracji — model dostaje ok=False i może się odbić.
            # (redundantnie wobec PluginTool, które już łapie PluginError/EgressError — DiD).
            return _err(tool, str(exc))

    def manual(self, allowed_names: list[str]) -> str:
        """Buduje deterministyczny „man" narzędzi TYLKO z allowlisty (nazwa/opis/akcje).

        Jedno źródło prawdy ze schematem dispatchu — model widzi dokładnie to, co może
        wywołać. Narzędzia nieznane/spoza rejestru są pomijane (nie da się ich wywołać).
        """
        lines: list[str] = []
        for name in allowed_names:
            kind = self._kind_of.get(name)
            if name not in self._tools or kind is None:
                continue
            actions = self._registry.actions_for(kind)
            if not actions:
                continue
            desc = self._descriptions.get(name, "")
            header = f'- narzędzie "{name}" (rodzaj {kind})'
            lines.append(f"{header}: {desc}" if desc else header)
            for spec in actions.values():
                params = ", ".join(f"{k}: {v}" for k, v in spec.params.items()) or "(brak)"
                lines.append(f'    · akcja "{spec.action}" — {spec.summary} | args: {params}')
        return "\n".join(lines)

    def close(self) -> None:
        """Zwalnia zasoby narzędzi trzymających uchwyty (np. połączenie sqlite RAG).

        Best-effort: wywoływane przy PODMIANIE stacku (``/api/config/runtime``), by stary
        magazyn nie wyciekał uchwytu pliku. Błąd zamknięcia NIE może wywalić podmiany —
        łapiemy szeroko (ścieżka sprzątania), narzędzia bez ``close`` są pomijane.
        """
        for tool in self._tools.values():
            close = getattr(tool, "close", None)
            if callable(close):
                # Sprzątanie NIE może przerwać podmiany stacku — tłumimy błędy zamknięcia.
                with contextlib.suppress(Exception):
                    close()
