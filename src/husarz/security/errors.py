"""Wyjątki warstwy bezpieczeństwa."""

from __future__ import annotations


class SecurityError(Exception):
    """Bazowy wyjątek warstwy bezpieczeństwa."""


class RoeViolationError(SecurityError):
    """Akcja narusza Rules of Engagement (cel/okno/technika/tryb)."""


class AuditError(SecurityError):
    """Błąd integralności lub zapisu dziennika audytu."""


class AuthorizationError(SecurityError):
    """Brak uprawnień (RBAC) do żądanej operacji."""
