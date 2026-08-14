"""Budowa ``RagBackend`` z konfiguracji (``memory`` — słowny; ``embedding`` — wektorowy).

Magazyn wektorowy: ``in_memory`` (ulotny) lub ``sqlite`` (trwały, szyfrowany at-rest).
Ciężkie/zewnętrzne backendy (pgvector, mem0) wchodzą PÓŹNIEJ jako lazy-importowane adaptery
za tym samym Protocol, za twardymi bramkami adopcji — patrz ADR-0017.
"""

from __future__ import annotations

from pathlib import Path

from husarz.config.schema import RagBackendConfig, SecurityConfig
from husarz.config.secrets import SecretsProvider
from husarz.memory.backend import EmbeddingRagBackend
from husarz.memory.crypto import build_cipher
from husarz.memory.embedder import EmbeddingTransport, build_embedder
from husarz.memory.errors import RagBackendError
from husarz.memory.sqlite_store import SqliteVectorStore
from husarz.memory.store import InMemoryVectorStore, VectorStore
from husarz.ssrf import HostResolver
from husarz.tools.rag import InMemoryRagBackend, RagBackend


def _build_store(
    config: RagBackendConfig,
    security: SecurityConfig,
    *,
    data_dir: Path,
    secrets: SecretsProvider | None,
) -> VectorStore:
    """Buduje magazyn wektorów. Sqlite: szyfrowanie at-rest fail-closed (klucz z secret-ref)."""
    if config.store == "in_memory":
        return InMemoryVectorStore(max_items=config.max_items)
    # sqlite — trwały. Rozstrzygnij politykę at-rest (None → dziedziczy z security).
    resolved_encrypt = (
        config.encrypt_at_rest
        if config.encrypt_at_rest is not None
        else security.encryption.at_rest
    )
    if security.encryption.at_rest and config.encrypt_at_rest is False:
        raise RagBackendError(
            "security.encryption.at_rest=true zabrania rag.encrypt_at_rest=false "
            "dla trwałego magazynu sqlite."
        )
    cipher = build_cipher(
        encrypt_at_rest=resolved_encrypt, key_ref=config.encryption_key_ref, secrets=secrets
    )
    path = config.path or (Path(data_dir) / "memory" / f"{config.collection}.db")
    return SqliteVectorStore(path, cipher, max_items=config.max_items)


def build_rag_backend(
    config: RagBackendConfig,
    security: SecurityConfig,
    *,
    data_dir: Path,
    transport: EmbeddingTransport | None = None,
    secrets: SecretsProvider | None = None,
    resolve: HostResolver | None = None,
) -> RagBackend:
    """Buduje backend pamięci wg ``config.backend``. Transport embeddera wstrzykiwalny (testy).

    ``resolve`` (opcjonalny) to resolver DNS dla pinowania IP endpointu embeddera —
    przewleczony jak w ``build_tools``/``build_git_service`` (ADR-0020).
    """
    if config.backend == "memory":
        return InMemoryRagBackend()
    if config.backend == "embedding":
        embedder = build_embedder(
            config.embedder,
            security.egress,
            transport=transport,
            secrets=secrets,
            resolve=resolve,
        )
        store = _build_store(config, security, data_dir=data_dir, secrets=secrets)
        return EmbeddingRagBackend(
            embedder, store, namespace=config.collection, dim=config.embedder.dim
        )
    raise RagBackendError(f"Nieznany backend pamięci: '{config.backend}'.")
