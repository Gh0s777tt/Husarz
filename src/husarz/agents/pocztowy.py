"""Pocztowy — lekki podwykonawca.

Wąskie, tanie zadania: pojedyncze wywołanie modelu, bez narzędzi i bez pętli
refleksji. Nie zyskuje pętli narzędziowej w Etapie 3 (celowo pozostaje prosty).
"""

from __future__ import annotations

from husarz.agents.base import BaseAgent


class Pocztowy(BaseAgent):
    """Lekki agent-podwykonawca Chorągwi."""
