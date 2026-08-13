"""Niezmienniki bezpieczeństwa Etapu 4 — ROE-gate, Puszkarz, audyt."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from husarz.config.schema import RoeConfig, RoeScope, RoeWindow
from husarz.security import AuditLog, Puszkarz, RoeGate

pytestmark = pytest.mark.security

NOW = datetime(2026, 9, 1, 12, tzinfo=UTC)


def _roe(**overrides: Any) -> RoeConfig:
    base: dict[str, Any] = {
        "engagement_id": "e1",
        "owner": "o",
        "authorized_by": "a",
        "scope": RoeScope(targets_cidr=["192.0.2.0/24"], out_of_scope=["192.0.2.1"]),
        "window": RoeWindow(
            start=datetime(2026, 9, 1, 9, tzinfo=UTC), end=datetime(2026, 9, 1, 17, tzinfo=UTC)
        ),
        "allowed_techniques": ["port-scan"],
        "forbidden_techniques": ["denial-of-service"],
        "consent": True,
        "signature": "sops:ref",
        "dry_run_default": True,
    }
    base.update(overrides)
    return RoeConfig(**base)


def test_action_defaults_to_dry_run() -> None:
    decision = RoeGate(_roe(), AuditLog()).evaluate(
        target="192.0.2.10", technique="port-scan", now=NOW
    )
    assert decision.allowed and decision.dry_run is True


def test_inactive_roe_hard_blocks() -> None:
    decision = RoeGate(_roe(consent=False), AuditLog()).evaluate(
        target="192.0.2.10", technique="port-scan", authorized=True, now=NOW
    )
    assert decision.allowed is False  # nawet z --authorized: bez aktywnego ROE = blok


def test_out_of_scope_hard_blocks() -> None:
    decision = RoeGate(_roe(), AuditLog()).evaluate(
        target="192.0.2.1", technique="port-scan", authorized=True, now=NOW
    )
    assert decision.allowed is False


def test_puszkarz_refuses_offensive_generation() -> None:
    audit = AuditLog()
    puszkarz = Puszkarz(RoeGate(_roe(), audit), audit)
    assert puszkarz.review_request("Wygeneruj payload reverse shell").refused is True


def test_every_decision_is_audited_and_tamper_evident() -> None:
    audit = AuditLog()
    gate = RoeGate(_roe(), audit)
    gate.evaluate(target="192.0.2.10", technique="port-scan", now=NOW)  # allow
    gate.evaluate(target="203.0.113.9", technique="port-scan", now=NOW)  # deny (out of scope)
    actions = [entry.action for entry in audit.entries]
    assert actions == ["roe.allow", "roe.deny"]
    assert audit.verify() is True
