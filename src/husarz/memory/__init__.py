"""Pamięć długoterminowa (RAG) — suwerenna, wektorowa.

Produkcyjny ``RagBackend`` za NIEZMIENIONYM protokołem z ``husarz.tools.rag``:
``EmbeddingRagBackend`` składa wstrzykiwalne szwy ``Embedder`` (tekst → wektor, domyślnie
lokalny Ollama, egress-gated) + ``VectorStore`` (cosine, izolacja namespace, cap+FIFO).
Domyślny backend pozostaje słowny (``memory``) — wektorowy (``embedding``) jest opt-in.

Trwałość (SQLite) i szyfrowanie at-rest wchodzą w Etapie 14b — RAZEM z przewleczeniem
sekretów do produkcji (bez tego szyfrowanie byłoby teatrem). Patrz ADR-0017.
"""

from __future__ import annotations

from husarz.memory.backend import EmbeddingRagBackend
from husarz.memory.builder import build_rag_backend
from husarz.memory.embedder import (
    Embedder,
    EmbeddingTransport,
    FakeEmbedder,
    HttpxEmbeddingTransport,
    OllamaEmbedder,
    build_embedder,
)
from husarz.memory.errors import EmbedderError, MemoryError_, RagBackendError
from husarz.memory.store import Hit, InMemoryVectorStore, VectorStore, cosine

__all__ = [
    "Embedder",
    "EmbeddingRagBackend",
    "EmbeddingTransport",
    "EmbedderError",
    "FakeEmbedder",
    "Hit",
    "HttpxEmbeddingTransport",
    "InMemoryVectorStore",
    "MemoryError_",
    "OllamaEmbedder",
    "RagBackendError",
    "VectorStore",
    "build_embedder",
    "build_rag_backend",
    "cosine",
]
