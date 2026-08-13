"""Towarzysz — agent pełny.

Pojedyncze wywołanie modelu (``BaseAgent.run``). Gdy agent ma ``tool_loop_enabled`` i
narzędzia, orkiestrator deleguje go przez pętlę narzędziową (function-calling) —
``husarz.agents.tool_loop``, ADR-0016 (Etap 13). Sama klasa pozostaje cienką bazą.
"""

from __future__ import annotations

from husarz.agents.base import BaseAgent


class Towarzysz(BaseAgent):
    """Agent pełny Chorągwi."""
