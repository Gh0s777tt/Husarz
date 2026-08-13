"""SqliteVectorStore — trwały magazyn wektorów (stdlib ``sqlite3``, bez serwera).

Jeden plik pod ``data_dir/memory/<collection>.db``. CAŁY rekord (``{id, vector, payload}``)
jest szyfrowany przez wstrzyknięty ``Cipher`` z ``AAD=namespace`` (patrz ``crypto``) —
na dysku nie ma jawnego tekstu, wektora ANI odcisku treści. Jawna kolumna ``id`` to
ZAŚLEPIONY klucz (``Cipher.blind_id`` — HMAC pod DEK), więc nie zdradza treści rekordu;
autorytatywny ``item_id`` żyje w zaszyfrowanym blobie. Scoring deszyfruje rekordy namespace
i liczy cosine w Pythonie (brute-force; koszt O(N) — stąd ``max_items``).

Izolacja: ``search``/``count`` filtrują po ``namespace`` (WHERE). Zapis atomowy pod zamkiem
(single-writer w puli wątków). ``check_same_thread=False`` + ``Lock`` = bezpieczne współbieżnie.
Błędy runtime ``sqlite3`` są opakowywane w ``RagBackendError`` (fail-closed, degradacja w pętli).
"""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any

from husarz.memory.crypto import Cipher, IdentityCipher
from husarz.memory.errors import RagBackendError
from husarz.memory.store import Hit, cosine


class SqliteVectorStore:
    """Trwały magazyn wektorów w SQLite z szyfrowaniem at-rest rekordu."""

    def __init__(
        self, path: str | Path, cipher: Cipher | None = None, *, max_items: int = 5000
    ) -> None:
        if max_items < 1:
            raise ValueError("max_items musi być >= 1.")
        self._cipher = cipher if cipher is not None else IdentityCipher()
        self._max_items = max_items
        self._lock = threading.Lock()
        db_path = Path(path)
        try:
            db_path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS memory ("
                "seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT, namespace TEXT, sealed BLOB, "
                "UNIQUE(namespace, id))"
            )
            self._conn.commit()
        except (OSError, sqlite3.Error) as exc:
            # Fail-closed z czytelnym komunikatem (np. read-only FS pod frozen binarką).
            raise RagBackendError(
                f"Nie można otworzyć magazynu pamięci '{db_path}': {exc}. "
                "Sprawdź uprawnienia data_dir."
            ) from exc

    def upsert(
        self, namespace: str, item_id: str, vector: list[float], payload: dict[str, Any]
    ) -> None:
        # Autorytatywny item_id ląduje W zaszyfrowanym blobie; jawna kolumna to zaślepiony klucz.
        record = json.dumps({"id": item_id, "vector": vector, "payload": payload}).encode("utf-8")
        aad = namespace.encode("utf-8")
        sealed = self._cipher.seal(record, aad=aad)
        row_key = self._cipher.blind_id(item_id, namespace=namespace)
        try:
            with self._lock:
                # Dedup: usuń istniejący (namespace,klucz), wstaw znów → na koniec (FIFO).
                self._conn.execute(
                    "DELETE FROM memory WHERE namespace = ? AND id = ?", (namespace, row_key)
                )
                self._conn.execute(
                    "INSERT INTO memory (id, namespace, sealed) VALUES (?, ?, ?)",
                    (row_key, namespace, sealed),
                )
                # Ewikcja FIFO: zostaw najnowsze ``max_items`` w tym namespace, usuń starsze.
                self._conn.execute(
                    "DELETE FROM memory WHERE namespace = ? AND seq NOT IN "
                    "(SELECT seq FROM memory WHERE namespace = ? ORDER BY seq DESC LIMIT ?)",
                    (namespace, namespace, self._max_items),
                )
                self._conn.commit()
        except sqlite3.Error as exc:
            raise RagBackendError(f"Błąd zapisu magazynu pamięci: {exc}.") from exc

    def search(self, namespace: str, vector: list[float], top_k: int) -> list[Hit]:
        try:
            with self._lock:
                rows = self._conn.execute(
                    "SELECT sealed FROM memory WHERE namespace = ?", (namespace,)
                ).fetchall()
        except sqlite3.Error as exc:
            raise RagBackendError(f"Błąd odczytu magazynu pamięci: {exc}.") from exc
        aad = namespace.encode("utf-8")
        hits: list[Hit] = []
        for (sealed,) in rows:
            record = json.loads(self._cipher.unseal(sealed, aad=aad))
            stored_vec = record["vector"]
            if len(stored_vec) != len(vector):
                # Fail-closed anty-korupcja: zmieniono model/wymiar embeddera pod istniejącym
                # trwałym magazynem — cosine dałby cichą 0.0 (śmieci) zamiast błędu.
                raise RagBackendError(
                    f"Niezgodny wymiar wektora w trwałym magazynie ({len(stored_vec)} != "
                    f"{len(vector)}) — najpewniej zmieniono model embeddera dla kolekcji "
                    f"'{namespace}'. Użyj osobnej kolekcji albo przebuduj pamięć."
                )
            hits.append(
                Hit(
                    item_id=str(record.get("id", "")),
                    score=cosine(vector, stored_vec),
                    payload=record["payload"],
                )
            )
        hits.sort(key=lambda hit: hit.score, reverse=True)
        return hits[: max(0, top_k)]

    def count(self, namespace: str) -> int:
        try:
            with self._lock:
                row = self._conn.execute(
                    "SELECT COUNT(*) FROM memory WHERE namespace = ?", (namespace,)
                ).fetchone()
        except sqlite3.Error as exc:
            raise RagBackendError(f"Błąd odczytu magazynu pamięci: {exc}.") from exc
        return int(row[0]) if row else 0

    def close(self) -> None:
        """Zamyka połączenie z bazą (do testów/porządku)."""
        with self._lock:
            self._conn.close()
