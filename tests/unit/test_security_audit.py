"""Testy niemodyfikowalnego dziennika audytu (łańcuch skrótów)."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from husarz.config.schema import AuditConfig, SecurityConfig
from husarz.security.audit import GENESIS_HASH, AuditLog, build_audit_log

pytestmark = pytest.mark.unit


class _Clock:
    def __init__(self) -> None:
        self._t = datetime(2026, 1, 1, tzinfo=UTC)

    def __call__(self) -> datetime:
        value = self._t
        self._t = self._t + timedelta(seconds=1)
        return value


def test_chain_links_and_verifies() -> None:
    log = AuditLog(clock=_Clock())
    first = log.record("operator", "start", {"x": 1})
    second = log.record("operator", "next", {"y": 2})

    assert first.prev_hash == GENESIS_HASH
    assert second.prev_hash == first.entry_hash
    assert log.head_hash == second.entry_hash
    assert log.verify() is True


def test_tampering_breaks_verification() -> None:
    log = AuditLog(clock=_Clock())
    log.record("operator", "a1", {"x": 1})
    log.record("operator", "a2", {"y": 2})
    assert log.verify() is True

    # Podmiana treści wcześniejszego wpisu (AuditEntry jest frozen -> replace).
    log._entries[0] = replace(log._entries[0], detail={"x": 999})  # noqa: SLF001
    assert log.verify() is False


def test_appends_to_file(tmp_path: Path) -> None:
    path = tmp_path / "audit" / "audit.log"
    log = AuditLog(path=path, clock=_Clock())
    log.record("operator", "a1", {})
    log.record("operator", "a2", {})
    assert path.exists()
    assert path.read_text(encoding="utf-8").count("\n") == 2


def test_roe_ref_recorded() -> None:
    log = AuditLog(clock=_Clock())
    entry = log.record("puszkarz", "roe.allow", {"target": "x"}, roe_ref="zlec-1")
    assert entry.roe_ref == "zlec-1"


def test_build_audit_log_from_config(tmp_path: Path) -> None:
    enabled = SecurityConfig(audit=AuditConfig(enabled=True, path=tmp_path / "a.log"))
    assert build_audit_log(enabled).path == tmp_path / "a.log"
    disabled = SecurityConfig(audit=AuditConfig(enabled=False))
    assert build_audit_log(disabled).path is None
