"""Test integracyjny routera na realnej, przykładowej konfiguracji repo.

Sprawdza wyłącznie selekcję (bez sieci) — modele w repo używają backendów
sieciowych, więc nie wykonujemy realnych wywołań.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from husarz.config import load_config
from husarz.router import ModelRouter

pytestmark = pytest.mark.integration


def test_router_selects_from_repo_config(repo_config_dir: Path) -> None:
    config = load_config(repo_config_dir)
    router = ModelRouter(config)

    # Agent 'husarz' -> glm-main (z routing.agent_models); glm-main i hermes są
    # wzajemnymi fallbackami, więc łańcuch to [glm-main, hermes].
    assert router.select(agent="husarz") == ["glm-main", "hermes"]

    # Zadanie polskie -> Bielik na pierwszym miejscu (reguła match_tags: [polish]).
    assert router.select(tags=["polish"])[0] == "bielik"

    # Agent 'bielik' -> bielik; fallback bielik=glm-main, a glm-main->hermes.
    assert router.select(agent="bielik") == ["bielik", "glm-main", "hermes"]
