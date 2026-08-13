"""Testy regresyjne dla poprawek Etapu 4 po adwersaryjnym przeglądzie."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from husarz.config.schema import AuditConfig, RoeConfig, RoeScope, RoeWindow, SecurityConfig
from husarz.config.secrets import VaultSecretsProvider
from husarz.security import AuditLog, Puszkarz, RoeGate
from husarz.security.audit import GENESIS_HASH, build_audit_log
from husarz.security.errors import AuditError

pytestmark = pytest.mark.unit

NOW = datetime(2026, 9, 1, 12, tzinfo=UTC)


def _fixed_clock() -> datetime:
    return datetime(2026, 1, 1, tzinfo=UTC)


def _roe(**overrides: Any) -> RoeConfig:
    base: dict[str, Any] = {
        "engagement_id": "e1",
        "owner": "o",
        "authorized_by": "a",
        "scope": RoeScope(targets_cidr=["192.0.2.0/24"], targets_domains=["app.example.local"]),
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


# --- ROE-gate / schema -----------------------------------------------------


def test_forbidden_technique_case_insensitive() -> None:
    roe = _roe(allowed_techniques=[], forbidden_techniques=["sqli"])
    decision = RoeGate(roe, AuditLog()).evaluate(target="192.0.2.5", technique="SQLI ", now=NOW)
    assert decision.allowed is False
    assert "zabroniona" in decision.reason


def test_cidr_with_host_bits_rejected() -> None:
    with pytest.raises(ValidationError, match="CIDR"):
        RoeScope(targets_cidr=["203.0.113.5/24"])


def test_naive_now_does_not_crash() -> None:
    naive_now = datetime(2026, 9, 1, 12)  # noqa: DTZ001 - celowo naive
    decision = RoeGate(_roe(), AuditLog()).evaluate(
        target="192.0.2.5", technique="port-scan", now=naive_now
    )
    assert decision.allowed is True


def test_whitespace_signature_is_inactive() -> None:
    assert _roe(signature="   ").is_active is False


def test_signature_verifier_can_block() -> None:
    gate = RoeGate(_roe(), AuditLog(), signature_verifier=lambda _roe_cfg: False)
    decision = gate.evaluate(target="192.0.2.5", technique="port-scan", now=NOW)
    assert decision.allowed is False
    assert "Podpis" in decision.reason


def test_empty_accountability_fields_rejected() -> None:
    with pytest.raises(ValidationError):
        _roe(owner="")


def test_empty_allowed_techniques_allows_all_but_forbidden() -> None:
    gate = RoeGate(_roe(allowed_techniques=[]), AuditLog())
    assert gate.evaluate(target="192.0.2.5", technique="dowolna", now=NOW).allowed is True
    assert (
        gate.evaluate(target="192.0.2.5", technique="denial-of-service", now=NOW).allowed is False
    )


def test_default_now_uses_real_clock() -> None:
    now = datetime.now(UTC)
    window = RoeWindow(start=now - timedelta(hours=1), end=now + timedelta(hours=1))
    gate = RoeGate(_roe(window=window), AuditLog())
    assert gate.evaluate(target="192.0.2.5", technique="port-scan").allowed is True


def test_authorized_does_not_override_forced_dry_run() -> None:
    gate = RoeGate(_roe(dry_run_default=True), AuditLog())
    decision = gate.evaluate(target="192.0.2.5", technique="port-scan", authorized=True, now=NOW)
    assert decision.allowed is True
    assert decision.dry_run is True  # dry_run_default wymusza dry-run mimo authorized


def test_scheme_prefixed_target_in_scope() -> None:
    gate = RoeGate(_roe(), AuditLog())
    assert (
        gate.evaluate(target="https://app.example.local/x", technique="port-scan", now=NOW).allowed
        is True
    )


# --- Audit log -------------------------------------------------------------


def test_record_persist_first_no_divergence(tmp_path: Path) -> None:
    log = AuditLog(path=tmp_path)  # katalog, nie plik -> zapis zawiedzie
    with pytest.raises(AuditError):
        log.record("op", "a", {})
    assert log.entries == []
    assert log.head_hash == GENESIS_HASH


def test_detail_deepcopied_after_record() -> None:
    log = AuditLog(clock=_fixed_clock)
    detail = {"ctx": {"k": "v"}}
    log.record("op", "a", detail)
    detail["ctx"]["k"] = "tampered"
    assert log.verify() is True  # mutacja wołającego nie zmienia wpisu


def test_non_serializable_detail_raises_audit_error() -> None:
    with pytest.raises(AuditError, match="serializowalne"):
        AuditLog().record("op", "a", {"bad": {1, 2, 3}})


def test_hmac_key_changes_hash_and_verifies() -> None:
    plain = AuditLog(clock=_fixed_clock)
    keyed = AuditLog(hmac_key=b"klucz", clock=_fixed_clock)
    plain_entry = plain.record("op", "a", {"x": 1})
    keyed_entry = keyed.record("op", "a", {"x": 1})
    assert plain_entry.entry_hash != keyed_entry.entry_hash
    assert plain.verify() and keyed.verify()


def test_load_from_file_and_detect_tampering(tmp_path: Path) -> None:
    path = tmp_path / "a.log"
    log = AuditLog(path=path, clock=_fixed_clock)
    log.record("op", "a1", {})
    log.record("op", "a2", {})
    assert AuditLog.load(path).verify() is True

    lines = path.read_text(encoding="utf-8").splitlines()
    first = json.loads(lines[0])
    first["action"] = "TAMPERED"
    lines[0] = json.dumps(first, ensure_ascii=False)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert AuditLog.load(path).verify() is False


def test_build_audit_log_restart_continuity(tmp_path: Path) -> None:
    path = tmp_path / "a.log"
    security = SecurityConfig(audit=AuditConfig(enabled=True, path=path))
    first_log = build_audit_log(security)
    first_log.record("op", "a1", {})

    second_log = build_audit_log(security)  # wczytuje istniejący plik
    assert second_log.head_hash == first_log.head_hash
    second_log.record("op", "a2", {})
    assert AuditLog.load(path).verify() is True


# --- Puszkarz --------------------------------------------------------------


def test_puszkarz_allows_defensive_yara_rule() -> None:
    audit = AuditLog()
    puszkarz = Puszkarz(RoeGate(_roe(), audit), audit)
    assert puszkarz.review_request("Napisz regułę YARA do wykrywania malware.").refused is False


def test_puszkarz_refuses_infinitive_verb_exploit() -> None:
    audit = AuditLog()
    puszkarz = Puszkarz(RoeGate(_roe(), audit), audit)
    review = puszkarz.review_request("Przygotuj mi działający exploit na tę usługę.")
    assert review.refused is True
    # Audyt zawiera skrót żądania, nie surową treść.
    detail = audit.entries[-1].detail
    assert "request_sha256" in detail
    assert "snippet" not in detail


# --- Sekrety ---------------------------------------------------------------


def test_vault_provider_handles_backend_error() -> None:
    def failing_read(path: str) -> dict[str, Any]:
        raise RuntimeError("vault niedostępny")

    assert VaultSecretsProvider(read=failing_read).resolve("vault:secret/x#token") is None


def test_vault_provider_missing_key_fragment() -> None:
    assert VaultSecretsProvider(read=lambda _p: {"t": "1"}).resolve("vault:bez-fragmentu") is None
