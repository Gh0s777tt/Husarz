"""Runtime ROE — spina zlecenia z konfiguracji z bramką, podpisem i orkiestratorem.

Do Etapu 4c bramka ``RoeGate`` była kompletna i przetestowana, ale **nieużywana**:
orkiestrator twardo pomijał każdego agenta z ``roe_required`` (Puszkarza), więc ani
ROE-gate, ani weryfikacja podpisu nie miały konsumenta. Ten moduł domyka wpięcie.

Co jest, a czego NIE ma
-----------------------
Poziom orkiestracji odpowiada na pytanie **„czy istnieje ważne zlecenie"**, a nie „czy
wolno zaatakować cel X". To rozróżnienie jest celowe: zadanie kroku planu to wolny tekst
od modelu, więc wyłuskiwanie z niego celu i techniki oznaczałoby autoryzację sterowaną
przez model — dokładnie to, przed czym ROE ma chronić. Autoryzacja NA CEL pozostaje
w ``RoeGate.evaluate`` i obowiązuje w chwili, gdy pojawia się konkretny cel (dziś: żaden
kod jej nie woła, bo Puszkarz nie ma zdolności wykonawczych — patrz niżej).

Puszkarz NIE dostaje narzędzi: pętla narzędziowa wyklucza agentów ``roe_required``
na poziomie L0 (``husarz.agents.tool_loop``), więc nawet pod ważnym zleceniem agent
wytwarza wyłącznie analizę tekstową, w trybie dry-run. Wpięcie nie nadaje zatem żadnej
nowej zdolności ofensywnej — zamienia „Puszkarz nie działa nigdy" na „Puszkarz działa
wyłącznie pod kryptograficznie zweryfikowanym zleceniem, bez narzędzi, w dry-run".

Kolejność bram przy delegacji:

1. **odmowa ofensywy** (``Puszkarz.review_request``) — bezwarunkowa, działa nawet bez zleceń,
2. **ważne zlecenie** (``RoeGate.engagement_decision``: zgoda + PODPIS + okno czasowe),
3. dopiero wtedy delegacja — z notatką kontekstową o trybie dry-run.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from husarz.config.schema import HusarzConfig
from husarz.config.secrets import SecretsProvider
from husarz.security.audit import AuditLog
from husarz.security.puszkarz import Puszkarz, PuszkarzReview
from husarz.security.roe_builder import build_roe_verifier
from husarz.security.roe_gate import RoeGate


@dataclass(slots=True, frozen=True)
class DelegationDecision:
    """Wynik oceny, czy wolno delegować krok do agenta wymagającego ROE."""

    allowed: bool
    reason: str
    engagement_id: str = ""
    dry_run: bool = True
    alternative: str = ""


class RoeRuntime:
    """Fasada ROE dla orkiestratora: przegląd żądania + wybór ważnego zlecenia."""

    def __init__(
        self, gates: dict[str, RoeGate], audit: AuditLog, *, actor: str = "puszkarz"
    ) -> None:
        self._gates = dict(gates)
        self._audit = audit
        self._actor = actor
        # Bramka dla Puszkarza jest nieistotna dla `review_request` (odmowa ofensywy to
        # reguła bezwarunkowa) — przekazujemy pierwszą dostępną albo None.
        first = self._gates[sorted(self._gates)[0]] if self._gates else None
        self._puszkarz = Puszkarz(first, audit, actor=actor)

    @property
    def engagements(self) -> list[str]:
        """Identyfikatory znanych zleceń (posortowane — determinizm)."""
        return sorted(self._gates)

    def review(self, text: str) -> PuszkarzReview:
        """Bezwarunkowy przegląd żądania (odmowa wytwarzania ofensywy)."""
        return self._puszkarz.review_request(text)

    def gate_for(self, engagement_id: str) -> RoeGate | None:
        """Zwraca bramkę zlecenia (do autoryzacji NA CEL), albo ``None``."""
        return self._gates.get(engagement_id)

    def authorize_delegation(self, task: str, *, now: datetime | None = None) -> DelegationDecision:
        """Ocenia, czy wolno delegować zadanie do agenta wymagającego ROE.

        Args:
            task: treść kroku planu (NIEZAUFANA — wyłącznie do przeglądu odmowy).
            now: chwila oceny (domyślnie teraz).

        Returns:
            Decyzja z identyfikatorem zlecenia, pod którym delegacja jest dozwolona.
            Odmowa jest już zapisana w audycie (przez ``Puszkarz``/``RoeGate``).
        """
        review = self.review(task)
        if review.refused:
            return DelegationDecision(
                allowed=False, reason=review.reason, alternative=review.alternative
            )
        if not self._gates:
            self._audit.record(self._actor, "roe.delegation_deny", {"reason": "brak zleceń"})
            return DelegationDecision(
                allowed=False,
                reason="Brak skonfigurowanego zlecenia ROE — agent bezpieczeństwa nieaktywny.",
            )
        # Pierwsze zlecenie, które przechodzi WSZYSTKIE bramy (zgoda + podpis + okno).
        # Odmowy poszczególnych zleceń audytuje sama bramka, więc ślad jest kompletny.
        for engagement_id in self.engagements:
            decision = self._gates[engagement_id].engagement_decision(now=now)
            if decision.allowed:
                self._audit.record(
                    self._actor,
                    "roe.delegation_allow",
                    {"dry_run": decision.dry_run},
                    roe_ref=engagement_id,
                )
                return DelegationDecision(
                    allowed=True,
                    reason=decision.reason,
                    engagement_id=engagement_id,
                    dry_run=decision.dry_run,
                )
        return DelegationDecision(
            allowed=False,
            reason=(
                "Żadne zlecenie ROE nie jest ważne (zgoda, podpis kryptograficzny "
                "i okno czasowe muszą być spełnione jednocześnie)."
            ),
        )


def build_roe_runtime(
    config: HusarzConfig, audit: AuditLog, *, secrets: SecretsProvider | None = None
) -> RoeRuntime:
    """Buduje ``RoeRuntime`` ze zleceń w konfiguracji, z weryfikacją podpisu (ADR-0021).

    Args:
        config: pełna konfiguracja (czyta ``config.roe`` i ``config.security.roe``).
        audit: dziennik audytu — KAŻDA decyzja ROE zostawia w nim ślad.
        secrets: dostawca sekretów do rozwiązania klucza weryfikującego podpis.

    Returns:
        Runtime z bramką per zlecenie. Brak zleceń = runtime, który zawsze odmawia
        delegacji (ale nadal wykonuje bezwarunkowy przegląd odmowy ofensywy).

    Weryfikator budujemy TYLKO wtedy, gdy istnieje zlecenie ze zgodą (``consent: true``) —
    ta sama semantyka, co w walidacji krzyżowej configu. Szablon bez zgody i tak nie przejdzie
    ``RoeConfig.is_active``, więc żądanie klucza od wdrożeń, które nie prowadzą testów, byłoby
    friction bez zysku. Gdy zgoda pojawi się później (np. nadpisaniem runtime), stack jest
    przebudowywany i weryfikator powstaje wtedy — z pełnym fail-closed.

    Raises:
        RoeSignatureError: jest zlecenie ze zgodą, a weryfikacja nie ma ``key_ref`` albo
            dostawcy sekretów — fail-closed przy starcie (lepiej nie wstać, niż wstać
            z bramką, która przepuszcza).
    """
    has_consent = any(roe.consent for roe in config.roe.values())
    verifier = build_roe_verifier(config.security, secrets) if has_consent else None
    gates = {
        engagement_id: RoeGate(roe, audit, signature_verifier=verifier)
        for engagement_id, roe in config.roe.items()
    }
    return RoeRuntime(gates, audit)
