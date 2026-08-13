"""Testy trwałości + szyfrowania at-rest pamięci (Etap 14b): crypto, SqliteVectorStore, bramki."""

from __future__ import annotations

from pathlib import Path

import pytest

from husarz.config.schema import (
    EmbedderConfig,
    EncryptionConfig,
    RagBackendConfig,
    SecurityConfig,
)
from husarz.memory import (
    AesGcmCipher,
    IdentityCipher,
    SqliteVectorStore,
    build_cipher,
    build_rag_backend,
)
from husarz.memory.errors import RagBackendError

pytestmark = pytest.mark.unit

_KEY32 = b"0123456789abcdef0123456789abcdef"


class DictSecrets:
    def __init__(self, values: dict[str, str]) -> None:
        self._values = values

    def resolve(self, ref: str) -> str | None:
        return self._values.get(ref)


# --- Cipher -----------------------------------------------------------------


def test_aesgcm_roundtrip() -> None:
    cipher = AesGcmCipher(_KEY32)
    sealed = cipher.seal(b"tajna tresc", aad=b"ns")
    assert sealed != b"tajna tresc"
    assert cipher.unseal(sealed, aad=b"ns") == b"tajna tresc"


def test_aesgcm_wrong_aad_rejected() -> None:
    # AAD=namespace: rekordu z kolekcji A nie da się odszyfrować jako kolekcji B (anti-swap).
    cipher = AesGcmCipher(_KEY32)
    sealed = cipher.seal(b"x", aad=b"agentA")
    with pytest.raises(RagBackendError):
        cipher.unseal(sealed, aad=b"agentB")


def test_aesgcm_bad_key_rejected() -> None:
    cipher = AesGcmCipher(_KEY32)
    sealed = cipher.seal(b"x", aad=b"ns")
    with pytest.raises(RagBackendError):
        AesGcmCipher(b"z" * 32).unseal(sealed, aad=b"ns")


def test_build_cipher_gates() -> None:
    assert isinstance(build_cipher(encrypt_at_rest=False, key_ref=None), IdentityCipher)
    with pytest.raises(RagBackendError):  # szyfrowanie bez klucza → fail-closed
        build_cipher(encrypt_at_rest=True, key_ref=None)
    with pytest.raises(RagBackendError):  # klucz nierozwiązywalny → fail-closed
        build_cipher(encrypt_at_rest=True, key_ref="env:BRAK", secrets=DictSecrets({}))
    cipher = build_cipher(
        encrypt_at_rest=True, key_ref="env:KEY", secrets=DictSecrets({"env:KEY": "sekret"})
    )
    assert isinstance(cipher, AesGcmCipher)


# --- SqliteVectorStore ------------------------------------------------------


def test_sqlite_roundtrip_and_namespace(tmp_path: Path) -> None:
    store = SqliteVectorStore(tmp_path / "m.db")
    store.upsert("a", "1", [1.0, 0.0], {"text": "w kolekcji A"})
    store.upsert("b", "1", [1.0, 0.0], {"text": "w kolekcji B"})
    hits = store.search("a", [1.0, 0.0], top_k=5)
    assert len(hits) == 1 and hits[0].payload["text"] == "w kolekcji A"  # izolacja namespace
    store.close()


def test_sqlite_persists_across_reopen(tmp_path: Path) -> None:
    path = tmp_path / "m.db"
    store = SqliteVectorStore(path)
    store.upsert("ns", "a", [1.0], {"text": "trwałe"})
    store.close()
    reopened = SqliteVectorStore(path)  # nowy proces/instancja
    hits = reopened.search("ns", [1.0], top_k=1)
    assert hits[0].payload["text"] == "trwałe"  # przetrwało restart (długoterminowość)
    reopened.close()


def test_sqlite_dedup_and_cap(tmp_path: Path) -> None:
    store = SqliteVectorStore(tmp_path / "m.db", max_items=2)
    store.upsert("ns", "a", [1.0], {"text": "1"})
    store.upsert("ns", "a", [1.0], {"text": "2"})  # dedup po id
    assert store.count("ns") == 1
    store.upsert("ns", "b", [1.0], {"text": "b"})
    store.upsert("ns", "c", [1.0], {"text": "c"})
    assert store.count("ns") == 2  # cap + ewikcja FIFO ('a' wyewiktowane)
    store.close()


def test_sqlite_encrypted_dedup_and_blinded_id(tmp_path: Path) -> None:
    # Przy szyfrowaniu: dedup działa mimo zaślepienia klucza, search zwraca AUTORYTATYWNY id
    # z blobu, a jawna kolumna id NIE jest surowym odciskiem treści (blind_id, nie sha256(text)).
    import hashlib

    store = SqliteVectorStore(tmp_path / "m.db", AesGcmCipher(_KEY32))
    item_id = hashlib.sha256(b"tresc rekordu").hexdigest()  # ścieżka produkcyjna
    store.upsert("ns", item_id, [1.0, 0.0], {"text": "tresc rekordu"})
    store.upsert("ns", item_id, [1.0, 0.0], {"text": "tresc rekordu"})  # ten sam id → dedup
    assert store.count("ns") == 1
    hits = store.search("ns", [1.0, 0.0], top_k=1)
    assert hits[0].item_id == item_id  # autorytatywny id odzyskany z zaszyfrowanego blobu
    assert item_id.encode("utf-8") not in (tmp_path / "m.db").read_bytes()  # kolumna zaślepiona
    store.close()


def test_sqlite_dim_mismatch_fails_closed(tmp_path: Path) -> None:
    # Zmiana modelu/wymiaru embeddera pod istniejącym trwałym magazynem → błąd, nie cicha 0.0.
    store = SqliteVectorStore(tmp_path / "m.db")
    store.upsert("ns", "a", [1.0, 0.0, 0.0], {"text": "stary wymiar 3"})
    with pytest.raises(RagBackendError):
        store.search("ns", [1.0, 0.0], top_k=1)  # zapytanie o wymiarze 2 vs zapisane 3
    store.close()


def test_atrest_fields_require_sqlite_and_embedding() -> None:
    # Walidacja krzyżowa: pola at-rest bez store=sqlite oraz store=sqlite bez backend=embedding.
    from pydantic import ValidationError

    with pytest.raises(ValidationError):  # klucz at-rest, ale magazyn ulotny → pole ignorowane
        RagBackendConfig(backend="embedding", encryption_key_ref="env:KEY")
    with pytest.raises(ValidationError):  # trwały magazyn bez wektorowego backendu → no-op
        RagBackendConfig(
            backend="memory", store="sqlite", embedder=EmbedderConfig(kind="fake", dim=8)
        )


# --- Bramki fail-closed w build_rag_backend ---------------------------------


def _sqlite_cfg(**kw: object) -> RagBackendConfig:
    base: dict[str, object] = {
        "backend": "embedding",
        "store": "sqlite",
        "embedder": EmbedderConfig(kind="fake", dim=8),
    }
    base.update(kw)
    return RagBackendConfig(**base)  # type: ignore[arg-type]


def test_sqlite_encrypt_without_key_fails_closed(tmp_path: Path) -> None:
    # security.encryption.at_rest=true (domyślnie) + sqlite + brak klucza → błąd startu.
    with pytest.raises(RagBackendError):
        build_rag_backend(_sqlite_cfg(), SecurityConfig(), data_dir=tmp_path)


def test_sqlite_encrypt_with_key_builds(tmp_path: Path) -> None:
    backend = build_rag_backend(
        _sqlite_cfg(encryption_key_ref="env:KEY"),
        SecurityConfig(),
        data_dir=tmp_path,
        secrets=DictSecrets({"env:KEY": "sekret-klucza"}),
    )
    backend.add("hetman husarz")
    assert backend.search("hetman", top_k=1)


def test_global_at_rest_forbids_local_encrypt_false(tmp_path: Path) -> None:
    # Globalny at_rest=true nie może być wyłączony lokalnie dla trwałego magazynu.
    with pytest.raises(RagBackendError):
        build_rag_backend(_sqlite_cfg(encrypt_at_rest=False), SecurityConfig(), data_dir=tmp_path)


def test_sqlite_unencrypted_allowed_when_global_off(tmp_path: Path) -> None:
    sec = SecurityConfig(encryption=EncryptionConfig(at_rest=False))
    backend = build_rag_backend(_sqlite_cfg(encrypt_at_rest=False), sec, data_dir=tmp_path)
    backend.add("bez szyfrowania")  # IdentityCipher — dozwolony, bo at_rest wyłączony
    assert backend.search("bez", top_k=1)
