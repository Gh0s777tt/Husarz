"""Puszkarz — agent bezpieczeństwa (część runtime, Etap 4).

Dwie twarde zasady:
1. **Bez generowania ofensywy.** Żądania wytworzenia malware/exploita/technik
   omijania zabezpieczeń są odrzucane z propozycją działania defensywnego.
2. **Tylko przez ROE-gate.** Każda akcja na celu przechodzi przez ``RoeGate``
   (domyślnie dry-run), a decyzja jest audytowana.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime

from husarz.security.audit import AuditLog
from husarz.security.roe_gate import RoeDecision, RoeGate

DEFENSIVE_ALTERNATIVE = (
    "Zamiast tego mogę pomóc defensywnie: audyt konfiguracji, hardening, reguły "
    "detekcji (np. Sigma/YARA na bazie znanych IOC), łatanie podatności oraz plan "
    "reakcji na incydent."
)

# Czasowniki wytwarzania (PL/EN) i rzeczowniki ofensywne — odmowa gdy oba obecne
# i BRAK kontekstu defensywnego. To sygnał heurystyczny, nie pełny klasyfikator
# intencji — ostateczną ocenę może wzmocnić model (Etap dalszy).
_GENERATION_VERBS = (
    "napisz",
    "napisać",
    "przygotuj",
    "wygeneruj",
    "stwórz",
    "stworz",
    "zbuduj",
    "zaimplementuj",
    "skompiluj",
    "opracuj",
    "zakoduj",
    "dostarcz",
    "write",
    "generate",
    "create",
    "build",
    "develop",
    "implement",
    "compile",
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
    "shellcode",
    "webshell",
    "payload",
    "reverse shell",
    "meterpreter",
)
# Jawne prośby o omijanie zabezpieczeń — silny sygnał ofensywny.
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
# Kontekst defensywny — nie odmawiamy analizy/detekcji/obrony (redukcja false-positive).
_DEFENSIVE_MARKERS = (
    "wykry",
    "detekcj",
    "yara",
    "sigma",
    "hardening",
    "audyt",
    "analiz",
    "ioc",
    "obron",
    "monitor",
    "łatani",
    "reguł",
)


def _classify(lowered: str) -> str:
    """Zwraca sygnał: 'evasion' | 'offensive-generation' | '' (dozwolone)."""
    is_defensive = any(marker in lowered for marker in _DEFENSIVE_MARKERS)
    if any(marker in lowered for marker in _EVASION_MARKERS) and not is_defensive:
        return "evasion"
    has_verb = any(verb in lowered for verb in _GENERATION_VERBS)
    has_noun = any(noun in lowered for noun in _OFFENSIVE_NOUNS)
    if has_verb and has_noun and not is_defensive:
        return "offensive-generation"
    return ""


@dataclass(slots=True, frozen=True)
class PuszkarzReview:
    """Wynik oceny żądania pod kątem granic Puszkarza."""

    refused: bool
    reason: str = ""
    alternative: str = ""


class Puszkarz:
    """Runtime agenta bezpieczeństwa: odmowa ofensywy + akcje przez ROE-gate."""

    def __init__(self, gate: RoeGate | None, audit: AuditLog, *, actor: str = "puszkarz") -> None:
        # Bramka jest OPCJONALNA: odmowa wytwarzania ofensywy to reguła bezwarunkowa, która
        # musi działać także wtedy, gdy nie istnieje żadne zlecenie (wtedy `authorize_action`
        # odmawia z definicji — nie ma czego autoryzować).
        self._gate = gate
        self._audit = audit
        self._actor = actor

    def review_request(self, text: str, *, principal: str = "") -> PuszkarzReview:
        """Ocenia żądanie. Odmawia wytwarzania narzędzi ofensywnych.

        ``principal`` (kto zlecił) trafia do audytu obok sygnału odmowy — przy wielu
        kontach sam ``actor='puszkarz'`` nie odpowiada na pytanie, czyje to było żądanie.
        """
        signal = _classify(text.lower())
        if signal:
            # Do audytu trafia SKRÓT żądania i sygnał — nigdy surowa treść
            # (mogłaby zawierać sekrety/PII w niemodyfikowalnym logu).
            digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
            self._audit.record(
                self._actor,
                "puszkarz.refuse",
                {"request_sha256": digest, "signal": signal},
                principal=principal,
            )
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
        principal: str = "",
    ) -> RoeDecision:
        """Autoryzuje akcję na celu przez ROE-gate (domyślnie dry-run).

        Bez bramki (brak jakiegokolwiek zlecenia) zwraca ODMOWĘ — fail-closed.
        """
        if self._gate is None:
            self._audit.record(
                self._actor,
                "roe.deny",
                {"target": target[:128], "technique": technique[:64], "reason": "brak ROE"},
                principal=principal,
            )
            return RoeDecision(
                allowed=False, reason="Brak skonfigurowanego zlecenia ROE.", dry_run=True
            )
        return self._gate.evaluate(
            target=target, technique=technique, authorized=authorized, now=now, principal=principal
        )
