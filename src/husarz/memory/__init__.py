"""Pamięć długoterminowa (RAG) — suwerenna, wektorowa.

Produkcyjny ``RagBackend`` za NIEZMIENIONYM protokołem z ``husarz.tools.rag``:
``EmbeddingRagBackend`` składa wstrzykiwalne szwy ``Embedder`` (tekst → wektor, domyślnie
lokalny Ollama, egress-gated) + ``VectorStore`` (cosine, izolacja namespace, cap+FIFO).
Domyślny backend pozostaje słowny (``memory``) — wektorowy (``embedding``) jest opt-in.

Trwałość (``SqliteVectorStore``) i szyfrowanie at-rest (``AesGcmCipher``/``build_cipher``) są
DOSTARCZONE w Etapie 14b — RAZEM z przewleczeniem sekretów do produkcji (klucz z referencji).
Patrz ADR-0017 (wektorowa pamięć) i ADR-0018 (trwałość + at-rest).
"""

from __future__ import annotations

from husarz.memory.backend import EmbeddingRagBackend
from husarz.memory.builder import build_rag_backend
from husarz.memory.crypto import AesGcmCipher, Cipher, IdentityCipher, build_cipher
from husarz.memory.embedder import (
    Embedder,
    EmbeddingTransport,
    FakeEmbedder,
    HttpxEmbeddingTransport,
    OllamaEmbedder,
    build_embedder,
)
from husarz.memory.errors import EmbedderError, MemoryError_, RagBackendError
from husarz.memory.sqlite_store import SqliteVectorStore
from husarz.memory.store import Hit, InMemoryVectorStore, VectorStore, cosine

__all__ = [
    "AesGcmCipher",
    "Cipher",
    "Embedder",
    "EmbeddingRagBackend",
    "EmbeddingTransport",
    "EmbedderError",
    "FakeEmbedder",
    "Hit",
    "HttpxEmbeddingTransport",
    "IdentityCipher",
    "InMemoryVectorStore",
    "MemoryError_",
    "OllamaEmbedder",
    "RagBackendError",
    "SqliteVectorStore",
    "VectorStore",
    "build_cipher",
    "build_embedder",
    "build_rag_backend",
    "cosine",
]
