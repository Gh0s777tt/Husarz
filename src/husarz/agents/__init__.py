"""Rdzeń agentów — ładowarka z config/agents/*.yaml oraz klasy Towarzysz/Pocztowy.

Publiczne API:
    BaseAgent, Towarzysz, Pocztowy — klasy agentów,
    AgentResult                    — wynik pracy agenta,
    SupportsComplete               — protokół routera wymagany przez agentów,
    build_agents                   — ładowarka agentów z konfiguracji.
"""

from __future__ import annotations

from husarz.agents.base import AgentResult, BaseAgent, SupportsComplete
from husarz.agents.errors import AgentError, PromptNotFoundError
from husarz.agents.loader import build_agents
from husarz.agents.pocztowy import Pocztowy
from husarz.agents.towarzysz import Towarzysz

__all__ = [
    "AgentError",
    "AgentResult",
    "BaseAgent",
    "Pocztowy",
    "PromptNotFoundError",
    "SupportsComplete",
    "Towarzysz",
    "build_agents",
]
