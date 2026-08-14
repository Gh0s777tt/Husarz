"""Narzędzie rag — pamięć i wyszukiwanie.

Backend jest wstrzykiwalny. ``InMemoryRagBackend`` (dopasowanie słów) to domyślny,
zero-zależnościowy backend (``memory``); produkcyjny wektorowy backend to
``husarz.memory.EmbeddingRagBackend`` (``embedding``) za TYM SAMYM protokołem
(lokalny embedder + magazyn cosine). pgvector/mem0 to przyszłe adaptery (ADR-0017).

Awarie backendu (np. niedostępny embedder, egress) są degradowane do
``ToolResult(ok=False)`` w warstwie dispatchu — nie wywalają pętli narzędziowej.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from husarz.tools.base import ToolResult

DEFAULT_TOP_K = 8


@runtime_checkable
class RagBackend(Protocol):
    """Backend pamięci: zapis, wyszukiwanie i zwolnienie zasobów."""

    def add(self, text: str, metadata: dict[str, Any] | None = None) -> None: ...

    def search(self, query: str, top_k: int) -> list[dict[str, Any]]: ...

    def close(self) -> None: ...


class InMemoryRagBackend:
    """Prosty backend w pamięci — dopasowanie po współdzielonych słowach."""

    def __init__(self) -> None:
        self._documents: list[dict[str, Any]] = []

    def add(self, text: str, metadata: dict[str, Any] | None = None) -> None:
        self._documents.append({"text": text, "metadata": metadata or {}})

    def search(self, query: str, top_k: int) -> list[dict[str, Any]]:
        query_words = {word for word in query.lower().split() if word}
        scored: list[tuple[int, dict[str, Any]]] = []
        for document in self._documents:
            text_words = set(document["text"].lower().split())
            overlap = len(query_words & text_words)
            if overlap:
                scored.append((overlap, document))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [document for _, document in scored[:top_k]]

    def close(self) -> None:
        """Brak zasobów systemowych (tylko RAM) — no-op dla parytetu protokołu ``RagBackend``."""


class RagTool:
    """Zapis i wyszukiwanie w pamięci (backend wstrzykiwalny: słowny lub wektorowy)."""

    name = "rag"

    def __init__(self, backend: RagBackend, *, top_k: int = DEFAULT_TOP_K) -> None:
        self._backend = backend
        self._top_k = top_k

    def add(self, text: str, metadata: dict[str, Any] | None = None) -> ToolResult:
        self._backend.add(text, metadata)
        return ToolResult(self.name, ok=True, metadata={"stored": True})

    def search(self, query: str) -> ToolResult:
        results = self._backend.search(query, self._top_k)
        output = "\n".join(str(result["text"]) for result in results)
        return ToolResult(self.name, ok=True, output=output, metadata={"count": len(results)})

    def close(self) -> None:
        """Zwalnia zasoby backendu (np. połączenie sqlite trwałej pamięci)."""
        self._backend.close()
