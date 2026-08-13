"""Orkiestrator „Husarz" — pętla plan -> deleguj -> obserwuj -> refleksja -> synteza.

Publiczne API:
    Orchestrator, OrchestratorResult, Observation — orkiestracja i jej wynik,
    build_orchestrator                            — złożenie z konfiguracji,
    Plan/PlanStep/Reflection, parse_plan/parse_reflection — model i parsowanie planu,
    PHASE_* / *_INSTRUCTION                        — znaczniki i instrukcje faz.
"""

from __future__ import annotations

from husarz.orchestrator.husarz import (
    DEFAULT_ORCHESTRATOR,
    Observation,
    Orchestrator,
    OrchestratorError,
    OrchestratorResult,
    build_orchestrator,
)
from husarz.orchestrator.plan import (
    Plan,
    PlanStep,
    Reflection,
    parse_plan,
    parse_reflection,
)
from husarz.orchestrator.prompts import (
    PHASE_PLAN,
    PHASE_REFLECT,
    PHASE_SYNTH,
    PLAN_INSTRUCTION,
    REFLECT_INSTRUCTION,
    SYNTHESIS_INSTRUCTION,
)

__all__ = [
    "DEFAULT_ORCHESTRATOR",
    "PHASE_PLAN",
    "PHASE_REFLECT",
    "PHASE_SYNTH",
    "PLAN_INSTRUCTION",
    "REFLECT_INSTRUCTION",
    "SYNTHESIS_INSTRUCTION",
    "Observation",
    "Orchestrator",
    "OrchestratorError",
    "OrchestratorResult",
    "Plan",
    "PlanStep",
    "Reflection",
    "build_orchestrator",
    "parse_plan",
    "parse_reflection",
]
