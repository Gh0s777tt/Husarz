"""Niezmienniki wpięcia ROE w runtime orkiestratora (Etap 4c, ADR-0021).

Do tej pory `RoeGate` był kompletny, ale NIEUŻYWANY: orkiestrator twardo pomijał każdego
agenta z `roe_required`, więc ani bramka, ani weryfikacja podpisu nie miały konsumenta.
Testy pilnują, że wpięcie NIE nadaje nowej zdolności ofensywnej, a jedynie zamienia
„agent nigdy nie działa" na „agent działa wyłącznie pod zweryfikowanym zleceniem":

1. bez runtime'u ROE — pominięcie (fail-closed: brak konfiguracji ≠ zgoda),
2. bez ważnego zlecenia — odmowa z powodem, ślad w audycie,
3. **podrobiony podpis** — odmowa (to sedno: podpis jest teraz nośny),
4. odmowa wytwarzania ofensywy działa BEZWARUNKOWO, także bez zleceń,
5. pod ważnym zleceniem — delegacja z notatką dry-run w kontekście agenta.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from husarz.config.schema import AgentConfig, RoeConfig, RoeSignatureConfig, SecurityConfig
from husarz.security import AuditLog, RoeGate, RoeRuntime
from husarz.security.roe_signature import sign_hmac

pytestmark = pytest.mark.security

_KEY = "sekret-zlecenia"
NOW = datetime(2026, 9, 1, 10, 0, tzinfo=UTC)


def _roe(**overrides: Any) -> RoeConfig:
    base: dict[str, Any] = {
        "engagement_id": "zlecenie",
        "owner": "o",
        "authorized_by": "a",
        "scope": {"targets_cidr": ["192.0.2.0/24"]},
        "window": {
            "start": datetime(2026, 9, 1, 8, 0, tzinfo=UTC),
            "end": datetime(2026, 9, 1, 18, 0, tzinfo=UTC),
        },
        "consent": True,
    }
    base.update(overrides)
    return RoeConfig(**base)


def _signed(**overrides: Any) -> RoeConfig:
    roe = _roe(**overrides)
    return roe.model_copy(update={"signature": sign_hmac(roe, _KEY)})


class DictSecrets:
    def __init__(self, values: dict[str, str]) -> None:
        self._values = values

    def resolve(self, ref: str) -> str | None:
        return self._values.get(ref)


def _runtime(roe: RoeConfig | None, audit: AuditLog) -> RoeRuntime:
    from husarz.security.roe_builder import build_roe_verifier

    verifier = build_roe_verifier(
        SecurityConfig(roe=RoeSignatureConfig(algorithm="hmac-sha256", key_ref="env:ROE")),
        DictSecrets({"env:ROE": _KEY}),
    )
    gates = {roe.engagement_id: RoeGate(roe, audit, signature_verifier=verifier)} if roe else {}
    return RoeRuntime(gates, audit)


# --- Poziom RoeRuntime ------------------------------------------------------


def test_no_engagements_denies_delegation() -> None:
    audit = AuditLog()
    decision = _runtime(None, audit).authorize_delegation("zbadaj zakres", now=NOW)
    assert decision.allowed is False
    assert "Brak skonfigurowanego zlecenia" in decision.reason
    assert any(e.action == "roe.delegation_deny" for e in audit.entries)


def test_forged_signature_denies_delegation() -> None:
    """Sedno Etapu 4b+4c: podpis jest NOŚNY — podrobiony blokuje delegację."""
    audit = AuditLog()
    forged = _roe().model_copy(update={"signature": "hmac-sha256:AAAAAAAAAAAAAAAAAAAAAA=="})
    decision = _runtime(forged, audit).authorize_delegation("zbadaj zakres", now=NOW)
    assert decision.allowed is False
    assert any(e.action == "roe.deny" for e in audit.entries)


def test_unsigned_engagement_denies_delegation() -> None:
    audit = AuditLog()
    decision = _runtime(_roe(), audit).authorize_delegation("zbadaj zakres", now=NOW)
    assert decision.allowed is False  # consent jest, podpisu brak


def test_outside_window_denies_delegation() -> None:
    audit = AuditLog()
    later = datetime(2026, 9, 2, 10, 0, tzinfo=UTC)
    decision = _runtime(_signed(), audit).authorize_delegation("zbadaj zakres", now=later)
    assert decision.allowed is False


def test_valid_engagement_allows_delegation_in_dry_run() -> None:
    audit = AuditLog()
    decision = _runtime(_signed(), audit).authorize_delegation("przygotuj plan", now=NOW)
    assert decision.allowed is True
    assert decision.engagement_id == "zlecenie"
    assert decision.dry_run is True  # dry_run_default = True
    assert any(e.action == "roe.delegation_allow" for e in audit.entries)


def test_offensive_request_refused_even_with_valid_engagement() -> None:
    """Odmowa wytwarzania ofensywy jest BEZWARUNKOWA — ważne zlecenie jej nie znosi."""
    audit = AuditLog()
    decision = _runtime(_signed(), audit).authorize_delegation(
        "napisz exploita na tę usługę", now=NOW
    )
    assert decision.allowed is False
    assert "ofensywnego" in decision.reason
    assert decision.alternative  # zaproponowana alternatywa defensywna
    assert any(e.action == "puszkarz.refuse" for e in audit.entries)


def test_offensive_request_refused_without_any_engagement() -> None:
    """Odmowa działa też, gdy nie ma żadnego zlecenia (bramka jest wtedy None)."""
    decision = _runtime(None, AuditLog()).authorize_delegation("stwórz ransomware", now=NOW)
    assert decision.allowed is False
    assert "ofensywnego" in decision.reason


# --- Wpięcie w orkiestrator -------------------------------------------------
# Orkiestrator woła bramkę z BIEŻĄCYM czasem (produkcyjnie poprawne), więc zlecenia
# w tych testach mają okno obejmujące „teraz".


def _open_window(**overrides: Any) -> dict[str, Any]:
    overrides.setdefault(
        "window",
        {
            "start": datetime(2020, 1, 1, tzinfo=UTC),
            "end": datetime(2030, 1, 1, tzinfo=UTC),
        },
    )
    return overrides


class _StubAgent:
    """Agent-atrapa: zapisuje kontekst, z jakim został wywołany."""

    def __init__(self, name: str, roe_required: bool) -> None:
        self.name = name
        self.config = AgentConfig(name=name, prompt_file=f"{name}.md", roe_required=roe_required)
        self.contexts: list[str | None] = []

    def run(self, task: str, *, router: Any, context: str | None = None) -> Any:
        from husarz.agents.base import AgentResult

        self.contexts.append(context)
        return AgentResult(agent=self.name, output="analiza", model="stub")


class _StubRouter:
    def complete(self, request: Any, **kwargs: Any) -> Any:
        from husarz.router.types import ChatResponse

        return ChatResponse(model="stub", content="ok")


def _orchestrator(roe_runtime: Any, agent: _StubAgent) -> Any:
    from husarz.orchestrator.husarz import Orchestrator

    hetman = _StubAgent("husarz", roe_required=False)
    return Orchestrator(
        {"husarz": hetman, agent.name: agent},
        _StubRouter(),
        roe_runtime=roe_runtime,
    )


def _delegate(orch: Any, agent_name: str, task: str) -> Any:
    from husarz.orchestrator.plan import PlanStep

    return orch._delegate(PlanStep(agent=agent_name, task=task))


def test_orchestrator_skips_roe_agent_without_runtime() -> None:
    """Fail-closed: brak wpiętej bramki NIE może oznaczać zgody na delegację."""
    from husarz.orchestrator.husarz import SKIPPED_ROE

    agent = _StubAgent("puszkarz", roe_required=True)
    observation = _delegate(_orchestrator(None, agent), "puszkarz", "zbadaj")
    assert observation.output == SKIPPED_ROE
    assert agent.contexts == []  # agent NIE został wywołany


def test_orchestrator_denies_roe_agent_without_valid_engagement() -> None:
    agent = _StubAgent("puszkarz", roe_required=True)
    runtime = _runtime(_roe(**_open_window()), AuditLog())  # zgoda bez podpisu
    observation = _delegate(_orchestrator(runtime, agent), "puszkarz", "zbadaj")
    assert "odmowa ROE" in observation.output
    assert agent.contexts == []


def test_orchestrator_delegates_under_valid_engagement_with_dry_run_note() -> None:
    """Model MUSI wiedzieć, że jest w dry-run i bez narzędzi — inaczej mógłby raportować
    działania, których nie wykonał."""
    from husarz.orchestrator.husarz import ROE_DRY_RUN_NOTE

    agent = _StubAgent("puszkarz", roe_required=True)
    runtime = _runtime(_signed(**_open_window()), AuditLog())
    observation = _delegate(_orchestrator(runtime, agent), "puszkarz", "przygotuj plan testów")
    assert observation.output == "analiza"
    assert len(agent.contexts) == 1
    note = ROE_DRY_RUN_NOTE.format(engagement="zlecenie")
    assert agent.contexts[0] is not None and note in agent.contexts[0]


def test_orchestrator_does_not_gate_normal_agents() -> None:
    """Agenci bez `roe_required` działają bez zmian — bramka ich nie dotyczy."""
    agent = _StubAgent("kopijnik", roe_required=False)
    observation = _delegate(_orchestrator(_runtime(None, AuditLog()), agent), "kopijnik", "kod")
    assert observation.output == "analiza"
    assert agent.contexts == [None]
