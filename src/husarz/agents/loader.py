"""Ładowarka agentów z konfiguracji.

Buduje instancje agentów z ``config.agents`` (pliki ``config/agents/*.yaml``)
oraz promptów systemowych z ``prompts/*.md``. Klasa agenta wynika z pola
``agent_class`` (Towarzysz/Pocztowy) — dodanie agenta nie wymaga zmian w rdzeniu.
"""

from __future__ import annotations

from pathlib import Path

from husarz.agents.base import BaseAgent
from husarz.agents.errors import PromptNotFoundError
from husarz.agents.pocztowy import Pocztowy
from husarz.agents.towarzysz import Towarzysz
from husarz.config.schema import AgentClass, HusarzConfig


def _read_prompt(prompts_dir: Path, filename: str) -> str:
    path = prompts_dir / filename
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise PromptNotFoundError(
            f"Brak pliku promptu agenta: {path}. Utwórz go w katalogu prompts/."
        ) from exc


def build_agents(
    config: HusarzConfig,
    *,
    prompts_dir: str | Path,
    include_disabled: bool = False,
) -> dict[str, BaseAgent]:
    """Buduje mapę ``nazwa -> agent`` z konfiguracji i promptów.

    Args:
        config: Zwalidowana konfiguracja Husarza.
        prompts_dir: Katalog z promptami systemowymi (``prompts/``).
        include_disabled: Gdy ``True`` — ładuje też agentów wyłączonych.

    Raises:
        PromptNotFoundError: Brak pliku promptu wskazanego przez agenta.
    """
    prompts = Path(prompts_dir)
    agents: dict[str, BaseAgent] = {}
    for name, agent_config in config.agents.items():
        if not agent_config.enabled and not include_disabled:
            continue
        system_prompt = _read_prompt(prompts, agent_config.prompt_file)
        agent_cls: type[BaseAgent] = (
            Towarzysz if agent_config.agent_class is AgentClass.TOWARZYSZ else Pocztowy
        )
        agents[name] = agent_cls(agent_config, system_prompt)
    return agents
