"""Niemodyfikowalny dziennik audytu z łańcuchem skrótów (tamper-evidence).

Każdy wpis zawiera skrót ``sha256(prev_hash + kanoniczny_payload)``. Zmiana
dowolnego wcześniejszego wpisu unieważnia wszystkie kolejne skróty, więc
``verify`` wykrywa manipulację. Zapis jest tylko dopisujący (append-only).
Zegar jest wstrzykiwalny — testy są deterministyczne.
"""

from __future__ import annotations

import hashlib
import json
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


def _hash_entry(payload: str) -> str:
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(slots=True)
class AuditLog:
    """Dopisujący dziennik audytu z weryfikowalnym łańcuchem skrótów."""

    path: Path | None = None
    clock: Callable[[], datetime] = _default_clock
    _entries: list[AuditEntry] = field(default_factory=list)
    _last_hash: str = GENESIS_HASH

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
        """Dopisuje wpis i zwraca go. Aktualizuje łańcuch skrótów."""
        timestamp = self.clock().isoformat()
        safe_detail = dict(detail or {})
        payload = _payload(timestamp, actor, action, safe_detail, roe_ref, self._last_hash)
        entry_hash = _hash_entry(payload)
        entry = AuditEntry(
            timestamp=timestamp,
            actor=actor,
            action=action,
            detail=safe_detail,
            roe_ref=roe_ref,
            prev_hash=self._last_hash,
            entry_hash=entry_hash,
        )
        self._entries.append(entry)
        self._last_hash = entry_hash
        self._append_to_file(entry)
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
            if _hash_entry(payload) != entry.entry_hash:
                return False
            prev = entry.entry_hash
        return True

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
    """Buduje dziennik audytu z konfiguracji (ścieżka z ``security.audit``)."""
    audit = security.audit
    path = Path(audit.path) if audit.enabled else None
    return AuditLog(path=path)
