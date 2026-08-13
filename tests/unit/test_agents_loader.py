"""Testy ładowarki agentów (build_agents)."""

from __future__ import annotations

from pathlib import Path

import pytest

from husarz.agents import Pocztowy, Towarzysz, build_agents
from husarz.agents.errors import PromptNotFoundError
from husarz.config import load_config

pytestmark = pytest.mark.unit

_MODELS = "default: m1\nregistry:\n  m1:\n    backend: mock\n    model: x\n"


def test_build_agents_from_repo_config(repo_config_dir: Path) -> None:
    config = load_config(repo_config_dir)
    prompts_dir = repo_config_dir.parent / "prompts"
    agents = build_agents(config, prompts_dir=prompts_dir)

    assert set(agents) == {
        "husarz",
        "bielik",
        "kopijnik",
        "zwiadowca",
        "puszkarz",
        "kanclerz",
        "chorazy",
    }
    assert isinstance(agents["husarz"], Towarzysz)
    assert isinstance(agents["chorazy"], Pocztowy)  # klasa 'pocztowy'
    assert "Bielik" in agents["bielik"].system_prompt


def test_missing_prompt_raises(write_config, tmp_path: Path) -> None:
    config_dir = write_config(
        {"models.yaml": _MODELS, "agents/x.yaml": "name: x\nprompt_file: brak.md\nmodel: auto\n"}
    )
    config = load_config(config_dir)
    with pytest.raises(PromptNotFoundError, match="brak.md"):
        build_agents(config, prompts_dir=tmp_path)  # pusty katalog — brak promptu


def test_disabled_agent_excluded(write_config, tmp_path: Path) -> None:
    agent_yaml = "name: y\nprompt_file: y.md\nmodel: auto\nenabled: false\n"
    config_dir = write_config({"models.yaml": _MODELS, "agents/y.yaml": agent_yaml})
    config = load_config(config_dir)
    # Agent wyłączony jest pomijany zanim loader spróbuje odczytać jego prompt.
    assert build_agents(config, prompts_dir=tmp_path) == {}
