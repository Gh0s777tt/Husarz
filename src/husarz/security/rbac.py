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
        {
            "config:read",
            "agent:run",
            "agent:puszkarz",
            "tool:*",
            "audit:read",
            "roe:authorize",
            "git:read",
            "git:write",
            "git:pr",
            "plugin:read",
            # Diagnoza instalacji (`GET /api/doctor`). ŚWIADOMIE osobne uprawnienie,
            # a nie `config:read`, z dwóch powodów:
            #   1. Odpowiedź niesie ENDPOINTY modeli i ŚCIEŻKI katalogów operatora —
            #      dane, których `config:read` celowo NIE wystawia (`/api/models`
            #      podaje backend i tagi, ale nie adres silnika).
            #   2. Wywołanie OTWIERA połączenia wychodzące do endpointów z konfiguracji,
            #      więc nie jest zwykłym odczytem stanu.
            # Rola `user` (zakładana samodzielną rejestracją) ma `config:read`, więc
            # oparcie diagnozy na nim wystawiłoby to wszystko publicznie.
            "diagnostics:read",
        }
    ),
    # Zwykły użytkownik (np. samodzielna rejestracja): może rozmawiać/orkiestrować,
    # ale NIE ma tool:*, roe:authorize (autoryzacja ofensywy), audit:read ani
    # diagnostics:read (diagnoza ujawnia endpointy i ścieżki operatora).
    # Najmniejsze uprawnienia dla kont zakładanych publicznie.
    "user": frozenset({"config:read", "agent:run"}),
    # `viewer` to PODGLĄD: świadomie bez `diagnostics:read`. Powody są DWA i trzeba je
    # rozróżniać, bo tylko jeden z nich już nie obowiązuje:
    #
    #   1. WOLUMEN RUCHU — pierwotny argument („podgląd nie wysyła pakietów"). Ten powód
    #      ZNIKNĄŁ wraz z limitem tempa: sufit ruchu wychodzącego instalacji to
    #      `security.diagnostics.max_requests_per_minute` × liczba endpointów, NIEZALEŻNIE
    #      od tego, ile ról ma uprawnienie. Dopisanie roli podnosi ten sufit o zero pakietów.
    #   2. UJAWNIENIE AKTUALNEJ TOPOLOGII — powód, który nie zmienił się wcale i jest
    #      dziś jedynym uzasadnieniem tej granicy. Odpowiedź diagnozy niesie adresy i porty
    #      silników, ścieżki katalogów operatora oraz KATALOG silnika, czyli nazwy modeli
    #      spoza konfiguracji Husarza (na współdzielonym serwerze wnioskowania — także
    #      cudzych). Dzieje się to na ścieżce SZCZĘŚLIWEJ, nie tylko przy awarii.
    #      `config:read` celowo tego nie daje: `/api/models` podaje backend i tagi, ale
    #      nie adres.
    #
    # Trzeci powód pojawił się razem z limitem: jest on GLOBALNY, więc konto podglądowe
    # odpytujące co dziesięć sekund trzyma kubełek na zerze i odbiera diagnozę operatorowi
    # dokładnie w trakcie awarii. Rozszerzenie kręgu ról wymaga więc NAJPIERW kubełka per
    # `principal` z rezerwą dla operatora (warunek wstępny zapisany w ROADMAP).
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
