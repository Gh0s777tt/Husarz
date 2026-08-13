"""Budowa ``RagBackend`` z konfiguracji (``memory`` — słowny; ``embedding`` — wektorowy).

Nowy backend = nowa gałąź tu + nowy plik (open/closed light). Ciężkie/zewnętrzne backendy
(pgvector, mem0) wchodzą PÓŹNIEJ jako lazy-importowane adaptery za tym samym Protocol,
za twardymi bramkami adopcji (telemetria off, egress, licencja) — patrz ADR-0017.
"""

from __future__ import annotations

from husarz.config.schema import EgressConfig, RagBackendConfig
from husarz.config.secrets import SecretsProvider
from husarz.memory.backend import EmbeddingRagBackend
from husarz.memory.embedder import EmbeddingTransport, build_embedder
from husarz.memory.errors import RagBackendError
from husarz.memory.store import InMemoryVectorStore
from husarz.tools.rag import InMemoryRagBackend, RagBackend


def build_rag_backend(
    config: RagBackendConfig,
    egress: EgressConfig,
    *,
    transport: EmbeddingTransport | None = None,
    secrets: SecretsProvider | None = None,
) -> RagBackend:
    """Buduje backend pamięci wg ``config.backend``. Transport embeddera wstrzykiwalny (testy)."""
    if config.backend == "memory":
        # Obecny backend słowny — zero zależności, brak powierzchni at-rest (dev/domyślny).
        return InMemoryRagBackend()
    if config.backend == "embedding":
        embedder = build_embedder(config.embedder, egress, transport=transport, secrets=secrets)
        store = InMemoryVectorStore(max_items=config.max_items)
        return EmbeddingRagBackend(
            embedder, store, namespace=config.collection, dim=config.embedder.dim
        )
    raise RagBackendError(f"Nieznany backend pamięci: '{config.backend}'.")
