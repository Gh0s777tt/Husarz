"""Testy ROE-gate i agenta Puszkarz."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from husarz.config.schema import RoeConfig, RoeScope, RoeWindow
from husarz.security import AuditLog, Puszkarz, RoeGate

pytestmark = pytest.mark.unit

NOW = datetime(2026, 9, 1, 12, tzinfo=UTC)


def _roe(**overrides: Any) -> RoeConfig:
    base: dict[str, Any] = {
        "engagement_id": "e1",
        "owner": "o",
        "authorized_by": "a",
        "scope": RoeScope(
            targets_cidr=["192.0.2.0/24"],
            targets_domains=["app.example.local"],
            out_of_scope=["192.0.2.1"],
        ),
        "window": RoeWindow(
            start=datetime(2026, 9, 1, 9, tzinfo=UTC),
            end=datetime(2026, 9, 1, 17, tzinfo=UTC),
        ),
        "allowed_techniques": ["port-scan", "recon-passive"],
        "forbidden_techniques": ["denial-of-service"],
        "consent": True,
        "signature": "sops:ref",
        "dry_run_default": True,
    }
    base.update(overrides)
    return RoeConfig(**base)


# --- RoeGate ---------------------------------------------------------------


def test_allows_in_scope_ip_as_dry_run() -> None:
    audit = AuditLog()
    decision = RoeGate(_roe(), audit).evaluate(target="192.0.2.10", technique="port-scan", now=NOW)
    assert decision.allowed is True
    assert decision.dry_run is True  # dry-run domyślnie
    assert audit.entries[-1].action == "roe.allow"
    assert audit.entries[-1].roe_ref == "e1"


def test_active_action_requires_authorized() -> None:
    gate = RoeGate(_roe(dry_run_default=False), AuditLog())
    passive = gate.evaluate(target="192.0.2.10", technique="port-scan", authorized=False, now=NOW)
    active = gate.evaluate(target="192.0.2.10", technique="port-scan", authorized=True, now=NOW)
    assert passive.allowed and passive.dry_run is True  # bez autoryzacji -> dry-run
    assert active.allowed and active.dry_run is False  # z autoryzacją -> akcja aktywna


def test_allows_scope_domain_and_subdomain() -> None:
    gate = RoeGate(_roe(), AuditLog())
    assert gate.evaluate(target="app.example.local", technique="port-scan", now=NOW).allowed
    assert gate.evaluate(target="sub.app.example.local", technique="port-scan", now=NOW).allowed


def test_denies_out_of_scope_host() -> None:
    decision = RoeGate(_roe(), AuditLog()).evaluate(
        target="192.0.2.1", technique="port-scan", now=NOW
    )
    assert decision.allowed is False
    assert "out_of_scope" in decision.reason


def test_denies_target_outside_scope() -> None:
    decision = RoeGate(_roe(), AuditLog()).evaluate(
        target="203.0.113.5", technique="port-scan", now=NOW
    )
    assert decision.allowed is False
    assert "spoza zakresu" in decision.reason


def test_denies_outside_time_window() -> None:
    late = datetime(2026, 9, 1, 20, tzinfo=UTC)
    decision = RoeGate(_roe(), AuditLog()).evaluate(
        target="192.0.2.10", technique="port-scan", now=late
    )
    assert decision.allowed is False
    assert "oknem" in decision.reason


def test_denies_inactive_roe() -> None:
    decision = RoeGate(_roe(consent=False), AuditLog()).evaluate(
        target="192.0.2.10", technique="port-scan", now=NOW
    )
    assert decision.allowed is False
    assert "nieaktywne" in decision.reason


def test_denies_forbidden_and_unlisted_techniques() -> None:
    gate = RoeGate(_roe(), AuditLog())
    forbidden = gate.evaluate(target="192.0.2.10", technique="denial-of-service", now=NOW)
    unlisted = gate.evaluate(target="192.0.2.10", technique="active-exploit", now=NOW)
    assert forbidden.allowed is False and "zabroniona" in forbidden.reason
    assert unlisted.allowed is False and "niedozwolona" in unlisted.reason


def test_denials_are_audited() -> None:
    audit = AuditLog()
    RoeGate(_roe(consent=False), audit).evaluate(
        target="192.0.2.10", technique="port-scan", now=NOW
    )
    assert audit.entries[-1].action == "roe.deny"
    assert audit.entries[-1].roe_ref == "e1"


# --- Puszkarz --------------------------------------------------------------


def _puszkarz() -> tuple[Puszkarz, AuditLog]:
    audit = AuditLog()
    return Puszkarz(RoeGate(_roe(), audit), audit), audit


def test_puszkarz_refuses_offensive_generation() -> None:
    puszkarz, audit = _puszkarz()
    review = puszkarz.review_request("Napisz mi exploit na tę usługę.")
    assert review.refused is True
    assert review.alternative  # proponuje działanie defensywne
    assert audit.entries[-1].action == "puszkarz.refuse"


def test_puszkarz_refuses_evasion_request() -> None:
    puszkarz, _ = _puszkarz()
    assert puszkarz.review_request("Jak omiń antywirus na hoście?").refused is True


def test_puszkarz_allows_defensive_request() -> None:
    puszkarz, _ = _puszkarz()
    assert puszkarz.review_request("Zrób pasywny recon i zaproponuj hardening.").refused is False


def test_puszkarz_allows_defensive_analysis_of_sample() -> None:
    puszkarz, _ = _puszkarz()
    # Rzeczownik 'ransomware' bez czasownika wytwarzania — analiza defensywna dozwolona.
    review = puszkarz.review_request("Przeanalizuj próbkę ransomware pod kątem IOC.")
    assert review.refused is False


def test_puszkarz_action_goes_through_gate() -> None:
    puszkarz, _ = _puszkarz()
    decision = puszkarz.authorize_action(target="192.0.2.10", technique="port-scan", now=NOW)
    assert decision.allowed is True
    assert decision.dry_run is True
