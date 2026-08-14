"""Testy zwalniania zasobów narzędzi (``close()``) — follow-up Etapu 14b.

Trwały magazyn RAG (``SqliteVectorStore``) trzyma połączenie do pliku; przy podmianie stacku
(``/api/config/runtime``) stara para musi być zamknięta, inaczej wyciek uchwytu. Sprawdzamy
łańcuch ``ToolDispatcher.close → RagTool → EmbeddingRagBackend → VectorStore`` oraz odporność.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from husarz.memory import (
    EmbeddingRagBackend,
    FakeEmbedder,
    InMemoryVectorStore,
    SqliteVectorStore,
)
from husarz.memory.errors import RagBackendError
from husarz.tools.dispatch import ToolDispatcher
from husarz.tools.rag import InMemoryRagBackend, RagTool

pytestmark = pytest.mark.unit


def test_dispatcher_close_closes_sqlite_rag_chain(tmp_path: Path) -> None:
    store = SqliteVectorStore(tmp_path / "m.db")
    backend = EmbeddingRagBackend(FakeEmbedder(dim=8), store, namespace="ns", dim=8)
    dispatcher = ToolDispatcher({"rag": RagTool(backend)}, {"rag": "rag"})
    backend.add("połączenie otwarte")  # działa przed zamknięciem
    dispatcher.close()
    # Połączenie zamknięte → sqlite3.ProgrammingError → opakowane w RagBackendError.
    with pytest.raises(RagBackendError):
        store.count("ns")


class _NoClose:
    name = "x"


class _Boom:
    name = "b"

    def close(self) -> None:
        raise RuntimeError("awaria zamknięcia")


def test_dispatcher_close_skips_missing_and_swallows_errors() -> None:
    # Narzędzie bez close() pominięte; błąd close() stłumiony (sprzątanie nie wywala podmiany).
    dispatcher = ToolDispatcher({"x": _NoClose(), "b": _Boom()}, {"x": "x", "b": "b"})
    dispatcher.close()  # nie rzuca


def test_inmemory_backends_close_are_noop(tmp_path: Path) -> None:
    InMemoryVectorStore().close()
    InMemoryRagBackend().close()
    RagTool(InMemoryRagBackend()).close()  # brak zasobu — no-op, nie rzuca


def test_sqlite_store_double_close_is_safe(tmp_path: Path) -> None:
    store = SqliteVectorStore(tmp_path / "m.db")
    store.close()
    store.close()  # idempotentne — drugie zamknięcie nie rzuca


def test_dispatch_after_close_degrades_to_ok_false(tmp_path: Path) -> None:
    # Niezmiennik degradacji: użycie ZAMKNIĘTEGO magazynu przez dispatch → ToolResult(ok=False),
    # nie wyjątek — żądanie w locie po podmianie stacku nie pada (RagBackendError ⊂ MemoryError_).
    store = SqliteVectorStore(tmp_path / "m.db")
    backend = EmbeddingRagBackend(FakeEmbedder(dim=8), store, namespace="ns", dim=8)
    dispatcher = ToolDispatcher({"rag": RagTool(backend)}, {"rag": "rag"})
    dispatcher.close()
    assert dispatcher.dispatch("rag", "search", {"query": "x"}).ok is False
