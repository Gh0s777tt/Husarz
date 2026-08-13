"""Testy pamięci długoterminowej (RAG) — Etap 14. Wszystko OFFLINE (fake embedder/transport)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from husarz.config.schema import EgressConfig, EgressPolicy, EmbedderConfig, RagBackendConfig
from husarz.memory import (
    EmbeddingRagBackend,
    FakeEmbedder,
    InMemoryVectorStore,
    OllamaEmbedder,
    build_embedder,
    build_rag_backend,
    cosine,
)
from husarz.memory.errors import EmbedderError, RagBackendError
from husarz.router.egress import EgressError

pytestmark = pytest.mark.unit


class FakeTransport:
    """Transport embeddera zwracający kanoniczny wektor; zapisuje, czy wywołany."""

    def __init__(self, dim: int = 4, status: int = 200) -> None:
        self.calls = 0
        self._dim = dim
        self._status = status

    def __call__(
        self, url: str, headers: dict[str, str], json: dict[str, Any], timeout: int
    ) -> tuple[int, Any]:  # noqa: E501
        self.calls += 1
        return self._status, {"embedding": [0.5] * self._dim}


# --- Embedder ---------------------------------------------------------------


def test_fake_embedder_deterministic_and_dim() -> None:
    emb = FakeEmbedder(dim=32)
    v1 = emb.embed(["hetman husarz"])[0]
    v2 = emb.embed(["hetman husarz"])[0]
    assert v1 == v2 and len(v1) == 32  # ten sam tekst → ten sam wektor
    assert abs(sum(x * x for x in v1) ** 0.5 - 1.0) < 1e-9  # L2-znormalizowany


def test_ollama_embedder_parses_and_validates_dim() -> None:
    transport = FakeTransport(dim=4)
    emb = OllamaEmbedder(
        "http://127.0.0.1:11434", "nomic", transport=transport, egress=EgressConfig(), dim=4
    )
    assert emb.embed(["x"])[0] == [0.5, 0.5, 0.5, 0.5]
    assert transport.calls == 1


def test_ollama_embedder_dim_mismatch_fails_closed() -> None:
    emb = OllamaEmbedder(
        "http://127.0.0.1:11434",
        "nomic",
        transport=FakeTransport(dim=8),
        egress=EgressConfig(),
        dim=4,
    )
    with pytest.raises(EmbedderError):
        emb.embed(["x"])  # serwer zwrócił dim 8, oczekiwano 4


def test_ollama_embedder_egress_blocks_wan_before_call() -> None:
    transport = FakeTransport()
    emb = OllamaEmbedder(
        "https://api.evil.com", "nomic", transport=transport, egress=EgressConfig(), dim=4
    )
    with pytest.raises(EgressError):
        emb.embed(["x"])  # WAN pod deny-all
    assert transport.calls == 0  # embedding NIE wyszedł na zewnątrz


def test_build_embedder_kinds() -> None:
    assert isinstance(
        build_embedder(EmbedderConfig(kind="fake", dim=8), EgressConfig()), FakeEmbedder
    )
    ollama = build_embedder(
        EmbedderConfig(kind="ollama", dim=4), EgressConfig(), transport=FakeTransport(dim=4)
    )
    assert isinstance(ollama, OllamaEmbedder)


# --- VectorStore ------------------------------------------------------------


def test_cosine_ranking() -> None:
    assert abs(cosine([1.0, 0.0], [1.0, 0.0]) - 1.0) < 1e-9  # identyczne
    assert abs(cosine([1.0, 0.0], [0.0, 1.0])) < 1e-9  # ortogonalne
    assert cosine([1.0], [1.0, 2.0]) == 0.0  # różna długość


def test_store_upsert_search_and_topk() -> None:
    store = InMemoryVectorStore()
    store.upsert("ns", "a", [1.0, 0.0], {"text": "a"})
    store.upsert("ns", "b", [0.0, 1.0], {"text": "b"})
    hits = store.search("ns", [1.0, 0.0], top_k=1)
    assert len(hits) == 1 and hits[0].item_id == "a"


def test_store_dedup_by_id() -> None:
    store = InMemoryVectorStore()
    store.upsert("ns", "a", [1.0, 0.0], {"text": "v1"})
    store.upsert("ns", "a", [0.0, 1.0], {"text": "v2"})
    assert store.count("ns") == 1  # ten sam id → jeden rekord


def test_store_cap_and_fifo_eviction() -> None:
    store = InMemoryVectorStore(max_items=2)
    for i in range(3):
        store.upsert("ns", f"id{i}", [float(i)], {"text": str(i)})
    assert store.count("ns") == 2  # najstarszy wyewiktowany
    assert store.search("ns", [0.0], top_k=10)  # id0 usunięty, zostały id1,id2
    assert all(h.item_id in {"id1", "id2"} for h in store.search("ns", [1.0], top_k=10))


def test_store_namespace_isolation() -> None:
    store = InMemoryVectorStore()
    store.upsert("agentA", "x", [1.0, 0.0], {"text": "sekret A"})
    assert store.search("agentB", [1.0, 0.0], top_k=10) == []  # inna kolekcja — brak wycieku
    assert store.count("agentB") == 0


# --- EmbeddingRagBackend ----------------------------------------------------


def test_embedding_backend_roundtrip() -> None:
    backend = EmbeddingRagBackend(
        FakeEmbedder(dim=64), InMemoryVectorStore(), namespace="ns", dim=64
    )
    backend.add("hetman husarz chorągiew", {"src": "test"})
    backend.add("zupełnie inny temat pogoda")
    results = backend.search("hetman husarz", top_k=1)
    assert results[0]["text"] == "hetman husarz chorągiew"  # najbliższe semantycznie (fake)
    assert results[0]["metadata"] == {"src": "test"}
    assert "score" in results[0]


def test_embedding_backend_dedup() -> None:
    store = InMemoryVectorStore()
    backend = EmbeddingRagBackend(FakeEmbedder(dim=16), store, namespace="ns", dim=16)
    backend.add("ten sam tekst")
    backend.add("ten sam tekst")
    assert store.count("ns") == 1  # dedup po sha256(text)


def test_embedding_backend_dim_mismatch_rejected() -> None:
    with pytest.raises(RagBackendError):
        EmbeddingRagBackend(FakeEmbedder(dim=8), InMemoryVectorStore(), namespace="ns", dim=16)


# --- Config + builder -------------------------------------------------------


def test_rag_backend_config_defaults_and_strict() -> None:
    from pydantic import ValidationError

    cfg = RagBackendConfig()
    assert cfg.backend == "memory" and cfg.collection == "husarz_memory" and cfg.top_k == 8
    with pytest.raises(ValidationError):
        RagBackendConfig(nieznane_pole=1)  # extra=forbid


def test_embedder_config_rejects_raw_key() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        EmbedderConfig(kind="ollama", api_key_ref="surowy-klucz")


def test_build_rag_backend_dispatch(tmp_path: Path) -> None:
    from husarz.config.schema import SecurityConfig
    from husarz.tools.rag import InMemoryRagBackend

    sec = SecurityConfig()
    mem = build_rag_backend(RagBackendConfig(backend="memory"), sec, data_dir=tmp_path)
    assert isinstance(mem, InMemoryRagBackend)
    emb = build_rag_backend(
        RagBackendConfig(backend="embedding", embedder=EmbedderConfig(kind="fake", dim=8)),
        sec,
        data_dir=tmp_path,
    )
    assert isinstance(emb, EmbeddingRagBackend)


def test_dispatch_degrades_on_backend_failure() -> None:
    # Awaria backendu RAG (np. niedostępny embedder) → ToolResult(ok=False), NIE wyjątek
    # (kontrakt dispatchu: pętla/orkiestracja nie pada przy transientnej awarii Ollamy).
    from husarz.router.egress import EgressError
    from husarz.tools.dispatch import ToolDispatcher
    from husarz.tools.rag import RagTool

    class RaisingBackend:
        def add(self, text: str, metadata: Any = None) -> None:
            raise EmbedderError("embedder niedostępny")

        def search(self, query: str, top_k: int) -> list[dict[str, Any]]:
            raise EgressError("egress zablokowany")

    disp = ToolDispatcher({"rag": RagTool(RaisingBackend())}, {"rag": "rag"})
    assert disp.dispatch("rag", "add", {"text": "x"}).ok is False
    assert disp.dispatch("rag", "search", {"query": "x"}).ok is False


def test_embedder_config_default_dim_matches_nomic() -> None:
    # Domyślny dim (768) pasuje do domyślnego modelu nomic-embed-text (spójność defaultów).
    assert EmbedderConfig().dim == 768


def test_build_rag_backend_allow_policy_embedding(tmp_path: Path) -> None:
    from husarz.config.schema import SecurityConfig

    # Pod polityką allow embedder ollama i tak buduje się poprawnie (transport wstrzyknięty).
    cfg = RagBackendConfig(backend="embedding", embedder=EmbedderConfig(kind="ollama", dim=4))
    sec = SecurityConfig(egress=EgressConfig(default_policy=EgressPolicy.ALLOW))
    backend = build_rag_backend(cfg, sec, data_dir=tmp_path, transport=FakeTransport(dim=4))
    backend.add("tekst")  # egress allow → transport użyty
    assert backend.search("tekst", top_k=1)
