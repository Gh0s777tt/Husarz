"""Niemodyfikowalny dziennik audytu z łańcuchem skrótów (tamper-evidence).

Każdy wpis zawiera skrót ``sha256(prev_hash + kanoniczny_payload)``. Zmiana
dowolnego wcześniejszego wpisu unieważnia wszystkie kolejne skróty, więc
``verify`` wykrywa manipulację. Zapis jest tylko dopisujący (append-only).
Zegar jest wstrzykiwalny — testy są deterministyczne.
"""

from __future__ import annotations

import copy
import hashlib
import hmac
import json
import threading
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from husarz.config.schema import SecurityConfig
from husarz.security.errors import AuditError

GENESIS_HASH = "0" * 64


def _default_clock() -> datetime:
    return datetime.now(UTC)


@dataclass(slots=True, frozen=True)
class AuditEntry:
    """Pojedynczy, niezmienny wpis audytu."""

    timestamp: str
    actor: str
    action: str
    detail: dict[str, Any]
    roe_ref: str | None
    prev_hash: str
    entry_hash: str


def _payload(
    timestamp: str,
    actor: str,
    action: str,
    detail: dict[str, Any],
    roe_ref: str | None,
    prev_hash: str,
) -> str:
    return json.dumps(
        {
            "timestamp": timestamp,
            "actor": actor,
            "action": action,
            "detail": detail,
            "roe_ref": roe_ref,
            "prev_hash": prev_hash,
        },
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )


@dataclass(slots=True)
class AuditLog:
    """Dopisujący dziennik audytu z weryfikowalnym łańcuchem skrótów.

    Bez ``hmac_key`` łańcuch (goły SHA-256) jest tamper-evident wobec przypadkowej
    korekty. Z ``hmac_key`` (klucz spoza logu) staje się odporny także na
    zmotywowanego edytora bez klucza — zalecane w produkcji.
    """

    path: Path | None = None
    clock: Callable[[], datetime] = _default_clock
    hmac_key: bytes | None = None
    _entries: list[AuditEntry] = field(default_factory=list)
    _last_hash: str = GENESIS_HASH
    # Serializuje dopisywanie: endpointy FastAPI (zwykłe ``def``) biegną w puli
    # wątków, więc read-modify-write łańcucha skrótów musi być atomowe — inaczej
    # dwa równoległe wpisy dostają ten sam ``prev_hash`` i ``verify`` daje fałszywy
    # alarm manipulacji. Wykluczony z porównań/reprezentacji dataclass.
    _lock: threading.Lock = field(default_factory=threading.Lock, compare=False, repr=False)

    def _compute_hash(self, payload: str) -> str:
        data = payload.encode("utf-8")
        if self.hmac_key is not None:
            return hmac.new(self.hmac_key, data, hashlib.sha256).hexdigest()
        return hashlib.sha256(data).hexdigest()

    @property
    def entries(self) -> list[AuditEntry]:
        """Kopia listy wpisów (tylko do odczytu z zewnątrz)."""
        return list(self._entries)

    @property
    def head_hash(self) -> str:
        """Skrót ostatniego wpisu (lub genesis, gdy pusty)."""
        return self._last_hash

    def record(
        self,
        actor: str,
        action: str,
        detail: dict[str, Any] | None = None,
        *,
        roe_ref: str | None = None,
    ) -> AuditEntry:
        """Dopisuje wpis i zwraca go. Zapis do pliku PRZED mutacją pamięci."""
        # Głęboka kopia — treść nie może zmienić się po zahashowaniu (niezmienność).
        safe_detail = copy.deepcopy(dict(detail or {}))
        # Cała sekwencja (odczyt prev_hash → hash → zapis pliku → mutacja pamięci)
        # jest krytyczna sekcją: gwarantuje ciągłość łańcucha pod współbieżnością.
        with self._lock:
            timestamp = self.clock().isoformat()
            try:
                payload = _payload(timestamp, actor, action, safe_detail, roe_ref, self._last_hash)
            except (TypeError, ValueError) as exc:
                raise AuditError(f"Szczegóły audytu nie są serializowalne do JSON: {exc}") from exc
            entry = AuditEntry(
                timestamp=timestamp,
                actor=actor,
                action=action,
                detail=safe_detail,
                roe_ref=roe_ref,
                prev_hash=self._last_hash,
                entry_hash=self._compute_hash(payload),
            )
            # Persist-first: jeśli zapis pliku zawiedzie, stan w pamięci NIE rozjeżdża się.
            self._append_to_file(entry)
            self._entries.append(entry)
            self._last_hash = entry.entry_hash
        return entry

    def verify(self) -> bool:
        """Sprawdza integralność łańcucha skrótów. ``True`` = brak manipulacji."""
        prev = GENESIS_HASH
        for entry in self._entries:
            if entry.prev_hash != prev:
                return False
            payload = _payload(
                entry.timestamp, entry.actor, entry.action, entry.detail, entry.roe_ref, prev
            )
            if self._compute_hash(payload) != entry.entry_hash:
                return False
            prev = entry.entry_hash
        return True

    @classmethod
    def load(cls, path: str | Path, *, hmac_key: bytes | None = None) -> AuditLog:
        """Wczytuje dziennik z pliku JSONL i odtwarza łańcuch (do ``verify``)."""
        source = Path(path)
        log = cls(path=None, hmac_key=hmac_key)
        for line in source.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            entry = AuditEntry(**json.loads(line))
            log._entries.append(entry)
            log._last_hash = entry.entry_hash
        return log

    def _append_to_file(self, entry: AuditEntry) -> None:
        if self.path is None:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(asdict(entry), ensure_ascii=False) + "\n")
        except OSError as exc:  # pragma: no cover - rzadka ścieżka I/O
            raise AuditError(f"Nie można dopisać do audytu: {exc}") from exc


def build_audit_log(security: SecurityConfig) -> AuditLog:
    """Buduje dziennik audytu z konfiguracji (ścieżka z ``security.audit``).

    Gdy plik już istnieje, odtwarza z niego łańcuch — dopiski po restarcie procesu
    pozostają ciągłe (bez fałszywego genesis w środku pliku).
    """
    audit = security.audit
    if not audit.enabled:
        return AuditLog(path=None)
    path = Path(audit.path)
    log = AuditLog(path=path)
    if path.exists():
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    entry = AuditEntry(**json.loads(line))
                    log._entries.append(entry)
                    log._last_hash = entry.entry_hash
        except (OSError, ValueError):  # pragma: no cover - uszkodzony plik audytu
            pass
    return log
