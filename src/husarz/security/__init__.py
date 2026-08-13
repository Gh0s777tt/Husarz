"""Bezpieczeństwo — audit log, ROE-gate, Puszkarz, RBAC (Etap 4).

Publiczne API:
    AuditLog / AuditEntry      — niemodyfikowalny dziennik z łańcuchem skrótów,
    RoeGate / RoeDecision      — twarda bramka ROE dla akcji Puszkarza,
    Puszkarz / PuszkarzReview  — agent bezpieczeństwa (odmowa ofensywy, ROE-gate),
    Rbac                       — autoryzacja oparta na rolach,
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
from husarz.security.roe_gate import RoeDecision, RoeGate

__all__ = [
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
    "RoeViolationError",
    "SecurityError",
    "build_audit_log",
]
