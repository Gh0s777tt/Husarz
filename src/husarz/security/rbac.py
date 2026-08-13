"""RBAC — kontrola dostępu oparta na rolach (czysta logika).

Rola mapowana jest na zbiór uprawnień w formie ``obszar:akcja`` (np. ``tool:shell``,
``config:write``). Wspierane wildcardy: ``*`` (wszystko) oraz ``obszar:*``.
mTLS/OIDC (uwierzytelnienie i przypisanie ról) wiąże się w Etapie 5 (API); tu jest
sama warstwa autoryzacji, w pełni testowalna.
"""

from __future__ import annotations

from collections.abc import Mapping

from husarz.security.errors import AuthorizationError

# Domyślny model uprawnień per rola (spójny z AuthConfig.roles).
DEFAULT_ROLE_PERMISSIONS: dict[str, frozenset[str]] = {
    "admin": frozenset({"*"}),
    "operator": frozenset(
        {"config:read", "agent:run", "agent:puszkarz", "tool:*", "audit:read", "roe:authorize"}
    ),
    "viewer": frozenset({"config:read", "audit:read"}),
}


class Rbac:
    """Autoryzacja operacji na podstawie roli."""

    def __init__(self, roles: Mapping[str, frozenset[str]] | None = None) -> None:
        self._roles: dict[str, frozenset[str]] = dict(roles or DEFAULT_ROLE_PERMISSIONS)

    def can(self, role: str, permission: str) -> bool:
        """Zwraca ``True``, gdy rola ma uprawnienie (z obsługą wildcardów)."""
        permissions = self._roles.get(role)
        if not permissions:
            return False
        if "*" in permissions or permission in permissions:
            return True
        area = permission.split(":", 1)[0]
        return f"{area}:*" in permissions

    def require(self, role: str, permission: str) -> None:
        """Rzuca ``AuthorizationError``, gdy rola nie ma uprawnienia."""
        if not self.can(role, permission):
            raise AuthorizationError(f"Rola '{role}' nie ma uprawnienia '{permission}'.")
