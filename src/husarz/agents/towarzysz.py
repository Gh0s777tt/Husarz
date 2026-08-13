"""Towarzysz — agent pełny.

W Etapie 2 działa jak baza (pojedyncze wywołanie modelu). W Etapie 3 zyska pętlę
narzędziową (function-calling) z dostępem do narzędzi z allowlisty w sandboxie.
"""

from __future__ import annotations

from husarz.agents.base import BaseAgent


class Towarzysz(BaseAgent):
    """Agent pełny Chorągwi."""
