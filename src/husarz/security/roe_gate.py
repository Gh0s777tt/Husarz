"""ROE-gate — twarda bramka autoryzacji akcji agenta Puszkarz.

WSZYSTKIE akcje Puszkarza przechodzą przez ``RoeGate.evaluate``. Sprawdzane są:
aktywność ROE (zgoda + podpis), okno czasowe, przynależność celu do zakresu
(CIDR/domeny, z wykluczeniami ``out_of_scope``), dozwolone/zabronione techniki
oraz tryb (domyślnie dry-run; akcja aktywna wymaga ``authorized=True``).
Każda decyzja jest logowana do audytu z odniesieniem do ROE.
"""

from __future__ import annotations

import ipaddress
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from husarz.config.net import endpoint_host
from husarz.config.schema import RoeConfig, RoeScope
from husarz.security.audit import AuditLog


@dataclass(slots=True, frozen=True)
class RoeDecision:
    """Wynik oceny ROE-gate."""

    allowed: bool
    reason: str
    dry_run: bool = True


def _as_network(entry: str) -> ipaddress.IPv4Network | ipaddress.IPv6Network | None:
    # strict=True: wpis z bitami hosta (np. 203.0.113.5/24) NIE jest siecią — nie
    # poszerzamy cicho zakresu; taki wpis nie dopasuje się (fail-closed).
    try:
        return ipaddress.ip_network(entry, strict=True)
    except ValueError:
        return None


def _host_of(target: str) -> str:
    """Wyłuskuje sam host z celu (obsługuje scheme://, userinfo i port)."""
    if "://" in target:
        parsed = endpoint_host(target)
        if parsed:
            return parsed.lower()
    return target.rsplit("@", 1)[-1].split(":", 1)[0].strip("[]").lower()


def _target_matches_entry(target: str, entry: str) -> bool:
    """Dopasowuje cel (IP lub domena) do wpisu zakresu (CIDR/IP lub domena)."""
    if target == entry:
        return True
    network = _as_network(entry)
    if network is not None:
        try:
            return ipaddress.ip_address(target) in network
        except ValueError:
            return False
    # Wpis jest domeną — dopasowanie dokładne lub jako subdomena.
    host = _host_of(target)
    domain = entry.lower()
    return host == domain or host.endswith(f".{domain}")


def _matches_any(target: str, entries: list[str]) -> bool:
    return any(_target_matches_entry(target, entry) for entry in entries)


def _in_scope(target: str, scope: RoeScope) -> bool:
    return _matches_any(target, scope.targets_cidr) or _matches_any(target, scope.targets_domains)


class RoeGate:
    """Bramka egzekwująca ROE dla akcji Puszkarza."""

    def __init__(
        self,
        roe: RoeConfig,
        audit: AuditLog,
        *,
        actor: str = "puszkarz",
        signature_verifier: Callable[[RoeConfig], bool] | None = None,
    ) -> None:
        self._roe = roe
        self._audit = audit
        self._actor = actor
        # Wstrzykiwalna, kryptograficzna weryfikacja podpisu ROE (np. przez dostawcę
        # sekretów). Gdy None — bramka wymaga jedynie obecności podpisu (is_active).
        self._signature_verifier = signature_verifier

    def _deny(self, target: str, technique: str, reason: str) -> RoeDecision:
        self._audit.record(
            self._actor,
            "roe.deny",
            {"target": target, "technique": technique, "reason": reason},
            roe_ref=self._roe.engagement_id,
        )
        return RoeDecision(allowed=False, reason=reason, dry_run=True)

    def evaluate(
        self,
        *,
        target: str,
        technique: str,
        authorized: bool = False,
        now: datetime | None = None,
    ) -> RoeDecision:
        """Ocenia dopuszczalność akcji i loguje decyzję. Domyślnie dry-run."""
        moment = now if now is not None else datetime.now(UTC)
        roe = self._roe

        if not roe.is_active:
            return self._deny(target, technique, "ROE nieaktywne (brak zgody lub podpisu).")
        if self._signature_verifier is not None and not self._signature_verifier(roe):
            return self._deny(target, technique, "Podpis ROE nie przeszedł weryfikacji.")
        if not roe.is_active_at(moment):
            return self._deny(target, technique, "Poza oknem czasowym ROE.")
        if _matches_any(target, roe.scope.out_of_scope):
            return self._deny(target, technique, f"Cel '{target}' wyłączony (out_of_scope).")
        if not _in_scope(target, roe.scope):
            return self._deny(target, technique, f"Cel '{target}' spoza zakresu ROE.")

        # Techniki porównujemy po normalizacji (bez rozróżniania wielkości liter/spacji),
        # by wariant 'SQLI'/' sqli ' nie omijał listy zabronionych.
        tech = technique.strip().lower()
        forbidden = {t.strip().lower() for t in roe.forbidden_techniques}
        allowed = {t.strip().lower() for t in roe.allowed_techniques}
        if tech in forbidden:
            return self._deny(target, technique, f"Technika '{technique}' zabroniona w ROE.")
        if allowed and tech not in allowed:
            return self._deny(target, technique, f"Technika '{technique}' niedozwolona w ROE.")

        # Domyślnie dry-run; akcja aktywna wymaga jawnej autoryzacji operatora.
        dry_run = roe.dry_run_default or not authorized
        self._audit.record(
            self._actor,
            "roe.allow",
            {
                "target": target,
                "technique": technique,
                "dry_run": dry_run,
                "authorized": authorized,
            },
            roe_ref=roe.engagement_id,
        )
        reason = "Dozwolone (dry-run)." if dry_run else "Dozwolone (akcja aktywna)."
        return RoeDecision(allowed=True, reason=reason, dry_run=dry_run)
