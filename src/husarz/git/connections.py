"""Magazyn połączeń Git (wstrzykiwalny: pamięć / plik JSON).

Przechowuje wyłącznie metadane połączenia (w tym ``token_ref`` — referencję do
sekretu), NIGDY samego tokenu. Zapis pliku jest atomowy (temp + ``os.replace``) pod
zamkiem — bezpieczny przy współbieżności (endpointy FastAPI w puli wątków).
"""

from __future__ import annotations

import contextlib
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

    @property
    def persistent(self) -> bool:
        """Czy połączenia przeżywają restart procesu.

        Pole istnieje, bo trwałość magazynu połączeń MUSI być zgodna z trwałością
        magazynu sekretów. Sekret jest zawsze zapisywany na dysk, więc kreator przy
        ULOTNYM magazynie połączeń tworzyłby przy każdym restarcie sekret osierocony:
        połączenie znika, token zostaje. Wołający sprawdza to jawnie zamiast zgadywać
        po typie obiektu.
        """
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

    @property
    def persistent(self) -> bool:  # noqa: D102 - patrz Protocol
        return False


class FileGitConnectionStore(InMemoryGitConnectionStore):
    """Magazyn połączeń w pliku JSON (trwałość; zapis atomowy pod zamkiem)."""

    def __init__(self, path: str | Path) -> None:
        super().__init__()
        self._path = Path(path)
        # Serializuje mutację+zapis (endpointy FastAPI w puli wątków) — atomowo.
        self._file_lock = threading.Lock()
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            for item in data.get("connections", []):
                conn = GitConnection(
                    name=item["name"],
                    provider=GitProviderKind(item["provider"]),
                    api_base=item["api_base"],
                    token_ref=item["token_ref"],
                    username=item.get("username"),
                    # `.get`, nie `[...]`: pliki zapisane przed dodaniem pola muszą się
                    # wczytywać. Aktualizacja Husarza nie może unieruchomić istniejących
                    # połączeń — objawiłoby się to jako „nie można wczytać połączeń".
                    ca_bundle=item.get("ca_bundle"),
                )
                self._by_name[conn.name] = conn
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
            raise GitConnectionError(f"Nie można wczytać połączeń z {self._path}: {exc}") from exc

    @property
    def persistent(self) -> bool:  # noqa: D102 - patrz Protocol
        return True

    def _persist(self, connections: dict[str, GitConnection]) -> None:
        """Zapisuje PODANY zestaw połączeń atomowo.

        Świadomie parametr, a nie ``self._by_name``: stan w pamięci podmieniamy dopiero PO
        udanym zapisie. Odwrotna kolejność (mutacja → zapis) zostawiała magazyn rozjechany —
        proces widział połączenie, którego w pliku nie było, więc znikało po restarcie.
        To ta sama wada, którą domknięto w magazynie sekretów; tutaj została przeoczona.

        ``OSError`` tłumaczymy na :class:`GitConnectionError`, bo wołający (kreator w API)
        łapie właśnie ten typ. Surowy ``OSError`` wymykał się jego obsłudze, więc awaria
        zapisu dawała 500 i — co gorsza — POMIJAŁA sprzątanie świeżo zapisanego sekretu,
        zostawiając go osieroconym.

        Args:
            connections: Zestaw do utrwalenia.

        Raises:
            GitConnectionError: Gdy zapis się nie powiedzie.
        """
        payload = {"connections": [asdict(c) for c in connections.values()]}
        tmp = self._path.with_name(f"{self._path.name}.{os.getpid()}.tmp")
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(tmp, self._path)
        except OSError as exc:
            # Sprzątanie nie może SAMO rzucić: `unlink(missing_ok=True)` tłumi wyłącznie
            # FileNotFoundError, a gdy katalog nadrzędny nie istnieje (albo jest plikiem),
            # leci NotADirectoryError — i wymykał się tej obsłudze, przesłaniając
            # właściwą przyczynę awarii.
            with contextlib.suppress(OSError):
                tmp.unlink(missing_ok=True)
            raise GitConnectionError(f"Nie można zapisać połączeń do {self._path}: {exc}") from exc

    def add(self, conn: GitConnection) -> None:  # noqa: D102
        with self._file_lock:  # cała para zapis+podmiana atomowa (inny zamek niż bazowy)
            if conn.name in self._by_name:
                raise GitConnectionError(f"Połączenie '{conn.name}' już istnieje.")
            kandydat = dict(self._by_name)
            kandydat[conn.name] = conn
            self._persist(kandydat)
            self._by_name = kandydat

    def remove(self, name: str) -> None:  # noqa: D102
        with self._file_lock:
            if name not in self._by_name:
                return
            kandydat = {k: v for k, v in self._by_name.items() if k != name}
            self._persist(kandydat)
            self._by_name = kandydat
