"""Magazyn połączeń Git (wstrzykiwalny: pamięć / plik JSON).

Przechowuje wyłącznie metadane połączenia (w tym ``token_ref`` — referencję do
sekretu), NIGDY samego tokenu. Zapis pliku jest atomowy (temp + ``os.replace``) pod
zamkiem — bezpieczny przy współbieżności (endpointy FastAPI w puli wątków).
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import asdict
from pathlib import Path
from typing import Protocol, runtime_checkable

from husarz.git.errors import GitConnectionError
from husarz.git.models import GitConnection, GitProviderKind


@runtime_checkable
class GitConnectionStore(Protocol):
    """Interfejs magazynu połączeń Git."""

    def add(self, conn: GitConnection) -> None:
        """Zapisuje NOWE połączenie. ``GitConnectionError`` przy kolizji nazwy."""
        ...

    def get(self, name: str) -> GitConnection | None:
        """Zwraca połączenie po nazwie albo ``None``."""
        ...

    def remove(self, name: str) -> None:
        """Usuwa połączenie (idempotentnie)."""
        ...

    def list_connections(self) -> list[GitConnection]:
        """Zwraca wszystkie połączenia (kopia)."""
        ...


class InMemoryGitConnectionStore:
    """Magazyn połączeń w pamięci (domyślny; dev/testy)."""

    def __init__(self) -> None:
        self._by_name: dict[str, GitConnection] = {}
        self._lock = threading.Lock()

    def add(self, conn: GitConnection) -> None:  # noqa: D102 - patrz Protocol
        with self._lock:
            if conn.name in self._by_name:
                raise GitConnectionError(f"Połączenie '{conn.name}' już istnieje.")
            self._by_name[conn.name] = conn

    def get(self, name: str) -> GitConnection | None:  # noqa: D102
        return self._by_name.get(name)

    def remove(self, name: str) -> None:  # noqa: D102
        with self._lock:
            self._by_name.pop(name, None)

    def list_connections(self) -> list[GitConnection]:  # noqa: D102
        return list(self._by_name.values())


class FileGitConnectionStore(InMemoryGitConnectionStore):
    """Magazyn połączeń w pliku JSON (trwałość; zapis atomowy pod zamkiem)."""

    def __init__(self, path: str | Path) -> None:
        super().__init__()
        self._path = Path(path)
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:  # pragma: no cover - uszkodzony plik
            raise GitConnectionError(f"Nie można wczytać połączeń z {self._path}: {exc}") from exc
        for item in data.get("connections", []):
            conn = GitConnection(
                name=item["name"],
                provider=GitProviderKind(item["provider"]),
                api_base=item["api_base"],
                token_ref=item["token_ref"],
                username=item.get("username"),
            )
            self._by_name[conn.name] = conn

    def _persist(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"connections": [asdict(c) for c in self._by_name.values()]}
        tmp = self._path.with_name(f"{self._path.name}.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self._path)

    def add(self, conn: GitConnection) -> None:  # noqa: D102
        super().add(conn)
        self._persist()

    def remove(self, name: str) -> None:  # noqa: D102
        super().remove(name)
        self._persist()
