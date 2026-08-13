"""Testy rejestru providerów narzędzi (open/closed) — Etap 12a.

Sprawdzają, że dispatch po ``kind`` przeszedł na ``ToolProviderRegistry`` bez zmiany
zachowania: 6 wbudowanych rodzajów, nieznany rodzaj -> ToolError (zachowany komunikat),
oraz że nowy rodzaj można dodać BEZ zmian w rdzeniu (wstrzyknięty rejestr).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from husarz.config import load_config
from husarz.tools import (
    BuildContext,
    Tool,
    ToolError,
    ToolProviderRegistry,
    ToolResult,
    build_tools,
    default_registry,
)

pytestmark = pytest.mark.unit


def test_default_registry_has_six_builtin_kinds() -> None:
    registry = default_registry()
    assert registry.known_kinds() == {
        "file_edit",
        "shell",
        "git",
        "run_tests",
        "web",
        "rag",
    }


def test_default_registry_is_fresh_instance() -> None:
    # Brak globalnego mutowalnego singletona — dwie instancje są niezależne.
    a = default_registry()
    b = default_registry()
    assert a is not b


def test_register_duplicate_kind_raises() -> None:
    registry = default_registry()
    with pytest.raises(ToolError, match="już zarejestrowany"):
        registry.register("web", lambda ctx: _StubTool("web"))


def test_get_unknown_kind_returns_none() -> None:
    assert default_registry().get("teleport") is None


class _StubTool:
    """Minimalne narzędzie testowe (spełnia protokół ``Tool``)."""

    def __init__(self, name: str) -> None:
        self.name = name

    def run(self) -> ToolResult:  # pragma: no cover - nieużywane w teście
        return ToolResult(self.name, ok=True)


def test_injected_registry_extends_without_core_change(
    write_config, tmp_path: Path  # noqa: ANN001
) -> None:
    # Nowy rodzaj 'custom' działa dzięki wstrzykniętemu rejestrowi — BEZ edycji build_tools.
    models = "default: m1\nregistry:\n  m1:\n    backend: mock\n    model: x\n"
    config_dir = write_config({"models.yaml": models, "tools/c.yaml": "name: moje\nkind: custom\n"})
    config = load_config(config_dir)

    registry = default_registry()
    built: list[BuildContext] = []

    def _build_custom(ctx: BuildContext) -> Tool:
        built.append(ctx)
        return _StubTool(ctx.name)

    registry.register("custom", _build_custom)
    tools = build_tools(config, workspace=tmp_path, registry=registry)
    assert set(tools) == {"moje"}
    assert built[0].name == "moje"  # builder dostał poprawny BuildContext
    assert built[0].tool_config.kind == "custom"


def test_empty_registry_reports_kind_as_unknown(
    write_config, tmp_path: Path
) -> None:  # noqa: ANN001
    # Rejestr bez rodzaju 'web' -> ten sam ToolError co dla nieznanego kind.
    models = "default: m1\nregistry:\n  m1:\n    backend: mock\n    model: x\n"
    config_dir = write_config({"models.yaml": models, "tools/w.yaml": "name: w\nkind: web\n"})
    config = load_config(config_dir)
    with pytest.raises(ToolError, match="Nieznany rodzaj narzędzia 'web'"):
        build_tools(config, workspace=tmp_path, registry=ToolProviderRegistry())
