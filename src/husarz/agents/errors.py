"""Wyjątki rdzenia agentów."""

from __future__ import annotations


class AgentError(Exception):
    """Bazowy wyjątek rdzenia agentów."""


class PromptNotFoundError(AgentError):
    """Brak pliku promptu systemowego agenta."""
