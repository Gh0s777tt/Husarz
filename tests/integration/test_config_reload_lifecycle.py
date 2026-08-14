"""Integracja: cykl życia trwałego magazynu RAG przy przebudowie stacku (0 sieci/DB).

Dowodzi, że ``POST /api/config/runtime`` zamyka STARY stack przy atomowej podmianie —
dwa kolejne rebuildy z ``store: sqlite`` NIE zostawiają otwartego starego połączenia
(follow-up z przeglądu Etapu 14b: wyciek uchwytu pliku sqlite; na Windows blokował
rotację ``data_dir``). Magazyn wektorowy: fake-embedder + IdentityCipher (at_rest off),
więc test jest w pełni OFFLINE i bez zależności ``cryptography``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from husarz.api import create_app
from husarz.config import load_config
from husarz.memory import SqliteVectorStore
from husarz.memory.errors import RagBackendError
from husarz.router import ChatResponse
from husarz.security import AuditLog

pytestmark = pytest.mark.integration


class _StubRouter:
    """Minimalny router — pozwala zbudować stack (pętlę narzędziową), bez sieci."""

    def complete(self, request: Any, *, agent: Any = None, model: Any = None, tags: Any = None):
        return ChatResponse(model="stub", content="ok")


def _sqlite_overrides(collection: str, tmp_path: Path) -> dict[str, Any]:
    # rag jako trwały magazyn sqlite (bez szyfrowania — at_rest off w profilu dev), plik
    # pod tmp_path/memory/<collection>.db. Różne kolekcje → osobne pliki (izolacja rebuildów).
    return {
        "platform": {"data_dir": str(tmp_path)},
        "security": {"encryption": {"at_rest": False}},
        "tools": {
            "rag": {
                "config": {
                    "backend": "embedding",
                    "store": "sqlite",
                    "collection": collection,
                    "embedder": {"kind": "fake", "dim": 16},
                }
            }
        },
    }


def test_config_runtime_closes_old_sqlite_store(
    repo_config_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Przechwyć KAŻDĄ zbudowaną instancję SqliteVectorStore, by po podmianie sprawdzić,
    # że stara ma zamknięte połączenie, a nowa działa.
    created: list[SqliteVectorStore] = []
    real_init = SqliteVectorStore.__init__

    def spy_init(self: SqliteVectorStore, *args: Any, **kwargs: Any) -> None:
        real_init(self, *args, **kwargs)
        created.append(self)

    monkeypatch.setattr(SqliteVectorStore, "__init__", spy_init)

    # Stack początkowy: rag = backend słowny 'memory' (repo) → BEZ sqlite (created puste).
    app = create_app(
        load_config(repo_config_dir),
        config_dir=repo_config_dir,
        audit=AuditLog(),
        router=_StubRouter(),
        prompts_dir=repo_config_dir.parent / "prompts",
    )
    client = TestClient(app)
    assert created == []  # słowny backend nie otwiera pliku

    # Rebuild #1 → sqlite S1 (kolekcja 'cykl_a'); stary stack (słowny) zamknięty no-op.
    r1 = client.post(
        "/api/config/runtime", json={"overrides": _sqlite_overrides("cykl_a", tmp_path)}
    )
    assert r1.json()["ok"] is True
    assert len(created) == 1

    # Rebuild #2 → sqlite S2 (kolekcja 'cykl_b'); PRZY podmianie zamykany jest STARY stack (S1).
    r2 = client.post(
        "/api/config/runtime", json={"overrides": _sqlite_overrides("cykl_b", tmp_path)}
    )
    assert r2.json()["ok"] is True
    assert len(created) == 2

    old_store, new_store = created
    # Niezmiennik: stare połączenie ZAMKNIĘTE — operacja na nim degraduje się do RagBackendError
    # (sqlite3.ProgrammingError „closed database" opakowany), a nie zwraca cichego wyniku.
    with pytest.raises(RagBackendError):
        old_store.count("cykl_a")
    # Nowe połączenie żyje (aktywny stack) — zwraca liczbę bez błędu.
    assert new_store.count("cykl_b") == 0

    # Motywacja Windows: bez otwartego uchwytu plik starej kolekcji daje się usunąć/zrotować.
    old_db = tmp_path / "memory" / "cykl_a.db"
    assert old_db.exists()
    old_db.unlink()  # brak PermissionError → uchwyt zwolniony (nie czeka na GC)
