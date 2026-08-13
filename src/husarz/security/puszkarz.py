"""Puszkarz — agent bezpieczeństwa (część runtime, Etap 4).

Dwie twarde zasady:
1. **Bez generowania ofensywy.** Żądania wytworzenia malware/exploita/technik
   omijania zabezpieczeń są odrzucane z propozycją działania defensywnego.
2. **Tylko przez ROE-gate.** Każda akcja na celu przechodzi przez ``RoeGate``
   (domyślnie dry-run), a decyzja jest audytowana.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from husarz.security.audit import AuditLog
from husarz.security.roe_gate import RoeDecision, RoeGate

DEFENSIVE_ALTERNATIVE = (
    "Zamiast tego mogę pomóc defensywnie: audyt konfiguracji, hardening, reguły "
    "detekcji (np. Sigma/YARA na bazie znanych IOC), łatanie podatności oraz plan "
    "reakcji na incydent."
)

# Czasowniki wytwarzania (PL/EN) i rzeczowniki ofensywne — odmowa gdy oba obecne.
_GENERATION_VERBS = (
    "napisz",
    "wygeneruj",
    "stwórz",
    "stworz",
    "zbuduj",
    "opracuj",
    "zakoduj",
    "dostarcz",
    "write",
    "generate",
    "create",
    "build",
    "develop",
    "code",
)
_OFFENSIVE_NOUNS = (
    "exploit",
    "malware",
    "ransomware",
    "rootkit",
    "keylogger",
    "trojan",
    "backdoor",
    "wirus",
    "worm",
    "stealer",
    "reverse shell",
    "meterpreter",
)
# Jawne prośby o omijanie zabezpieczeń — odmowa niezależnie od czasownika.
_EVASION_MARKERS = (
    "omiń antywirus",
    "obejście antywirusa",
    "obejscie antywirusa",
    "bypass antivirus",
    "bypass av",
    "bypass edr",
    "evade detection",
    "omiń edr",
    "ukryj przed edr",
)


def _is_offensive_generation(lowered: str) -> bool:
    if any(marker in lowered for marker in _EVASION_MARKERS):
        return True
    has_verb = any(verb in lowered for verb in _GENERATION_VERBS)
    has_noun = any(noun in lowered for noun in _OFFENSIVE_NOUNS)
    return has_verb and has_noun


@dataclass(slots=True, frozen=True)
class PuszkarzReview:
    """Wynik oceny żądania pod kątem granic Puszkarza."""

    refused: bool
    reason: str = ""
    alternative: str = ""


class Puszkarz:
    """Runtime agenta bezpieczeństwa: odmowa ofensywy + akcje przez ROE-gate."""

    def __init__(self, gate: RoeGate, audit: AuditLog, *, actor: str = "puszkarz") -> None:
        self._gate = gate
        self._audit = audit
        self._actor = actor

    def review_request(self, text: str) -> PuszkarzReview:
        """Ocenia żądanie. Odmawia wytwarzania narzędzi ofensywnych."""
        if _is_offensive_generation(text.lower()):
            self._audit.record(self._actor, "puszkarz.refuse", {"snippet": text[:200]})
            return PuszkarzReview(
                refused=True,
                reason="Żądanie dotyczy wytworzenia narzędzia ofensywnego — odmowa.",
                alternative=DEFENSIVE_ALTERNATIVE,
            )
        return PuszkarzReview(refused=False)

    def authorize_action(
        self,
        *,
        target: str,
        technique: str,
        authorized: bool = False,
        now: datetime | None = None,
    ) -> RoeDecision:
        """Autoryzuje akcję na celu przez ROE-gate (domyślnie dry-run)."""
        return self._gate.evaluate(
            target=target, technique=technique, authorized=authorized, now=now
        )
