"""Panel Agenci musi pokazywać model EFEKTYWNY, a nie pole z pliku agenta.

REGRESJA z uruchomienia realnej aplikacji: `routing.agent_models` jest centralną tabelą
routingu i ma pierwszeństwo nad `model` z `config/agents/*.yaml` — tak liczy router. Panel
czytał jednak wyłącznie plik agenta, więc po zmianie tabeli (w pliku albo nadpisaniem
runtime) `GET /api/agents` pokazywał operatorowi model, którego agent w ogóle nie użyje.

W dostarczonym szablonie obie wartości są zgodne, więc rozjazd był niewidoczny aż do
momentu, w którym ktoś użyje tabeli routingu zgodnie z jej przeznaczeniem.
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from husarz.api import create_app
from husarz.config import load_config
from husarz.router.selection import resolve_agent_model, select_candidates


def _agent_models(client: TestClient) -> dict[str, str]:
    return {a["name"]: a["model"] for a in client.get("/api/agents").json()}


# --- Reguła pierwszeństwa ---------------------------------------------------


def test_routing_table_wins_over_agent_file(repo_config_dir: Path) -> None:
    config = load_config(repo_config_dir)
    agent = next(iter(config.agents))
    target = config.models.default

    patched = config.model_copy(
        update={"routing": config.routing.model_copy(update={"agent_models": {agent: target}})}
    )
    assert resolve_agent_model(patched, agent) == target


def test_auto_in_table_falls_back_to_agent_file(repo_config_dir: Path) -> None:
    """``auto`` w tabeli oznacza „nie narzucam" — wtedy obowiązuje plik agenta."""
    config = load_config(repo_config_dir)
    agent = next(iter(config.agents))
    declared = config.agents[agent].model

    patched = config.model_copy(
        update={"routing": config.routing.model_copy(update={"agent_models": {agent: "auto"}})}
    )
    assert resolve_agent_model(patched, agent) == declared


def test_unknown_agent_returns_none(repo_config_dir: Path) -> None:
    assert resolve_agent_model(load_config(repo_config_dir), "nie-ma-takiego") is None


def test_router_and_panel_agree(repo_config_dir: Path) -> None:
    """Kluczowy niezmiennik: panel i router liczą model tą samą regułą.

    Bez tego każda zmiana pierwszeństwa w jednym miejscu po cichu rozjeżdża drugie.
    """
    config = load_config(repo_config_dir)
    for agent in config.agents:
        resolved = resolve_agent_model(config, agent)
        if resolved is None or resolved == "auto":
            continue
        assert select_candidates(config, agent=agent)[0] == resolved


# --- End-to-end przez API ---------------------------------------------------


def test_panel_reports_routed_model_not_agent_file(repo_config_dir: Path) -> None:
    """Gdy tabela routingu wskazuje INNY model niż plik agenta, panel pokazuje ten z tabeli."""
    config = load_config(repo_config_dir)
    agent = next(a for a, cfg in config.agents.items() if cfg.model != config.models.default)
    target = config.models.default
    assert config.agents[agent].model != target, "test musi porównywać dwie RÓŻNE wartości"

    patched = config.model_copy(
        update={"routing": config.routing.model_copy(update={"agent_models": {agent: target}})}
    )
    client = TestClient(create_app(patched, prompts_dir=repo_config_dir.parent / "prompts"))
    assert _agent_models(client)[agent] == target


def test_runtime_override_of_routing_is_visible_in_panel(repo_config_dir: Path) -> None:
    """Nadpisanie runtime zmienia model użyty przez orkiestratora — panel musi to odbić."""
    config = load_config(repo_config_dir)
    agent = next(a for a, cfg in config.agents.items() if cfg.model != config.models.default)
    target = config.models.default

    client = TestClient(
        create_app(
            config, prompts_dir=repo_config_dir.parent / "prompts", config_dir=repo_config_dir
        )
    )
    before = _agent_models(client)[agent]
    response = client.post(
        "/api/config/runtime",
        json={"overrides": {"routing": {"agent_models": {agent: target}}}},
    )
    assert response.status_code == 200 and response.json()["ok"] is True
    after = _agent_models(client)[agent]
    assert before != after, "test byłby pusty, gdyby nadpisanie niczego nie zmieniało"
    assert after == target
