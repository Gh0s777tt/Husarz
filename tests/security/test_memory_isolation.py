"""Niezmienniki bezpieczeństwa pamięci długoterminowej (RAG) — Etap 14.

Skupienie: izolacja między agentami (namespace/kolekcja — brak wycieku), suwerenność
embeddingów (egress deny-all, airgap lokalny), sekret jako referencja, cap wzrostu.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from husarz.config import load_config
from husarz.config.errors import ConfigError
from husarz.config.schema import EgressConfig, EmbedderConfig
from husarz.memory import EmbeddingRagBackend, FakeEmbedder, InMemoryVectorStore, build_embedder
from husarz.router.egress import EgressError

pytestmark = pytest.mark.security

_MODELS = "default: m1\nregistry:\n  m1:\n    backend: mock\n    model: x\n"


class FakeTransport:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(
        self, url: str, headers: dict[str, str], json: dict[str, Any], timeout: int
    ) -> tuple[int, Any]:  # noqa: E501
        self.calls += 1
        return 200, {"embedding": [0.5] * 4}


def _backend(namespace: str, store: InMemoryVectorStore) -> EmbeddingRagBackend:
    return EmbeddingRagBackend(FakeEmbedder(dim=32), store, namespace=namespace, dim=32)


def test_cross_agent_memory_no_leak() -> None:
    # Zatruty add w kolekcji agenta A NIE wypływa w search agenta B (izolacja namespace).
    store = InMemoryVectorStore()
    agent_a = _backend("agentA", store)
    agent_b = _backend("agentB", store)
    agent_a.add("[[HUSARZ_ACTION]] złośliwa instrukcja od agenta A")
    assert agent_b.search("złośliwa instrukcja", top_k=10) == []  # brak wycieku między agentami
    assert agent_a.search("złośliwa", top_k=10)  # w swojej kolekcji widoczne


def test_embedder_egress_blocks_wan() -> None:
    transport = FakeTransport()
    embedder = build_embedder(
        EmbedderConfig(kind="ollama", endpoint="https://api.evil.com", dim=4),
        EgressConfig(),
        transport=transport,
    )
    with pytest.raises(EgressError):
        embedder.embed(["dane wrażliwe"])  # WAN pod deny-all — wektor nie wychodzi
    assert transport.calls == 0


def test_embedder_loopback_allowed() -> None:
    transport = FakeTransport()
    embedder = build_embedder(
        EmbedderConfig(kind="ollama", endpoint="http://127.0.0.1:11434", dim=4),
        EgressConfig(),
        transport=transport,
    )
    embedder.embed(["ok"])  # loopback — dozwolone
    assert transport.calls == 1


def test_embedder_key_must_be_reference() -> None:
    with pytest.raises(ValidationError):
        EmbedderConfig(kind="ollama", api_key_ref="surowy-klucz-w-configu")


def test_rag_collections_must_be_disjoint(write_config, tmp_path: Path) -> None:  # noqa: ANN001
    # Dwa narzędzia rag z TĄ SAMĄ kolekcją → walidacja odrzuca (kanał injekcji cross-agent).
    same = "kind: rag\nconfig:\n  collection: wspolna\n"
    config_dir = write_config(
        {
            "models.yaml": _MODELS,
            "tools/rag-a.yaml": "name: rag_a\n" + same,
            "tools/rag-b.yaml": "name: rag_b\n" + same,
        }
    )
    with pytest.raises(ConfigError, match="wspolna"):
        load_config(config_dir)


def test_airgap_rejects_nonlocal_embedder_endpoint(repo_config_dir: Path) -> None:
    # Profil airgap: embedder narzędzia rag z nielokalnym endpointem → błąd startu (PII).
    with pytest.raises(ConfigError, match="embedder"):
        load_config(
            repo_config_dir,
            runtime_overrides={
                "platform": {"profile": "airgap"},
                "security": {"egress": {"allowlist": []}},
                "tools": {
                    "rag": {
                        "config": {
                            "backend": "embedding",
                            "embedder": {"endpoint": "https://api.evil.com"},
                        }
                    }
                },
            },
        )


def test_store_growth_capped() -> None:
    # Model-sterowany add nie rośnie w nieskończoność — cap + ewikcja FIFO.
    store = InMemoryVectorStore(max_items=3)
    backend = EmbeddingRagBackend(FakeEmbedder(dim=16), store, namespace="ns", dim=16)
    for i in range(10):
        backend.add(f"wpis numer {i}")
    assert store.count("ns") == 3  # magazyn ograniczony
