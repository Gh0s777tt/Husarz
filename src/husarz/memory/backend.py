"""EmbeddingRagBackend — wektorowa pamięć długoterminowa za NIEZMIENIONYM Protocol RagBackend.

Kompozycja wstrzykiwalnych szwów: ``Embedder`` (tekst → wektor) + ``VectorStore`` (upsert/
search po cosinusie). Drop-in za ``InMemoryRagBackend`` — ``RagTool`` (czyta tylko ``text``)
działa bez zmian; ``search`` zwraca nadzbiór: ``{text, metadata, score}``.

Bezpieczeństwo: izolacja przez ``namespace`` (kolekcja), dedup po ``sha256(text)`` (ten sam
tekst nie mnoży rekordów ani nie liczy embeddingu ponownie), fail-closed przy niezgodności
wymiaru embeddera (anty-korupcja magazynu przez pomieszanie modeli).
"""

from __future__ import annotations

import hashlib
from typing import Any

from husarz.memory.embedder import Embedder
from husarz.memory.errors import RagBackendError
from husarz.memory.store import VectorStore


class EmbeddingRagBackend:
    """Wektorowa pamięć: ``add`` embeduje i zapisuje, ``search`` embeduje zapytanie i szuka."""

    def __init__(self, embedder: Embedder, store: VectorStore, *, namespace: str, dim: int) -> None:
        if embedder.dim != dim:
            raise RagBackendError(
                f"Wymiar embeddera ({embedder.dim}) != embedding_dim ({dim}) — "
                "ujednolić konfigurację (anty-korupcja magazynu)."
            )
        self._embedder = embedder
        self._store = store
        self._namespace = namespace
        self._dim = dim

    def add(self, text: str, metadata: dict[str, Any] | None = None) -> None:
        vector = self._embedder.embed([text])[0]
        item_id = hashlib.sha256(text.encode("utf-8")).hexdigest()  # dedup po treści
        self._store.upsert(
            self._namespace, item_id, vector, {"text": text, "metadata": metadata or {}}
        )

    def search(self, query: str, top_k: int) -> list[dict[str, Any]]:
        vector = self._embedder.embed([query])[0]
        hits = self._store.search(self._namespace, vector, top_k)
        return [
            {
                "text": str(hit.payload.get("text", "")),
                "metadata": hit.payload.get("metadata", {}),
                "score": hit.score,
            }
            for hit in hits
        ]
