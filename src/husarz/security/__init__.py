"""Bezpieczeństwo — audit log, ROE-gate, Puszkarz, RBAC (Etap 4).

Publiczne API:
    AuditLog / AuditEntry      — niemodyfikowalny dziennik z łańcuchem skrótów,
    RoeGate / RoeDecision      — twarda bramka ROE dla akcji Puszkarza,
    Puszkarz / PuszkarzReview  — agent bezpieczeństwa (odmowa ofensywy, ROE-gate),
    Rbac                       — autoryzacja oparta na rolach,
    build_roe_verifier         — kryptograficzna weryfikacja podpisu ROE (ADR-0021),
    hierarchia SecurityError.

Runtime egress/sandbox, mTLS i OIDC wiążą się w Etapie 5 (API) — patrz ROADMAP.
"""

from __future__ import annotations

from husarz.security.audit import AuditEntry, AuditLog, build_audit_log
from husarz.security.errors import (
    AuditError,
    AuthorizationError,
    RoeViolationError,
    SecurityError,
)
from husarz.security.puszkarz import DEFENSIVE_ALTERNATIVE, Puszkarz, PuszkarzReview
from husarz.security.rbac import DEFAULT_ROLE_PERMISSIONS, Rbac
from husarz.security.roe_builder import RoeVerifier, build_roe_verifier
from husarz.security.roe_gate import RoeDecision, RoeGate
from husarz.security.roe_signature import (
    ALGORITHM_ED25519,
    ALGORITHM_HMAC,
    RoeSignatureError,
    canonical_payload,
    sign_ed25519,
    sign_hmac,
)

__all__ = [
    "ALGORITHM_ED25519",
    "ALGORITHM_HMAC",
    "DEFAULT_ROLE_PERMISSIONS",
    "DEFENSIVE_ALTERNATIVE",
    "AuditEntry",
    "AuditError",
    "AuditLog",
    "AuthorizationError",
    "Puszkarz",
    "PuszkarzReview",
    "Rbac",
    "RoeDecision",
    "RoeGate",
    "RoeSignatureError",
    "RoeVerifier",
    "RoeViolationError",
    "SecurityError",
    "build_audit_log",
    "build_roe_verifier",
    "canonical_payload",
    "sign_ed25519",
    "sign_hmac",
]
