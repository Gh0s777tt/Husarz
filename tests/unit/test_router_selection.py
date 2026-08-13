"""Testy czystej logiki wyboru modeli (selection.select_candidates)."""

from __future__ import annotations

import pytest

from husarz.router import select_candidates

pytestmark = pytest.mark.unit

# Rejestr bazowy: glm (kod) z fallbackiem hermes, bielik (polski), hermes (narzędzia).
_REGISTRY = {
    "glm": {
        "backend": "mock",
        "model": "glm",
        "tags": ["code", "reasoning"],
        "fallback": ["hermes"],
    },
    "bielik": {"backend": "mock", "model": "bielik", "tags": ["polish"]},
    "hermes": {"backend": "mock", "model": "hermes", "tags": ["tools"]},
}
_RULES = [{"match_tags": ["polish"], "prefer": ["bielik", "glm"]}]


def test_select_by_tags_prefers_rule_order(make_config) -> None:
    config = make_config(registry=_REGISTRY, default="glm", rules=_RULES)
    assert select_candidates(config, tags=["polish"]) == ["bielik", "glm", "hermes"]


def test_select_by_agent(make_config) -> None:
    config = make_config(registry=_REGISTRY, default="glm", agent_models={"husarz": "glm"})
    assert select_candidates(config, agent="husarz") == ["glm", "hermes"]


def test_select_explicit_model(make_config) -> None:
    config = make_config(registry=_REGISTRY, default="glm")
    assert select_candidates(config, model="bielik") == ["bielik"]


def test_select_default_when_no_hints(make_config) -> None:
    config = make_config(registry=_REGISTRY, default="glm")
    assert select_candidates(config) == ["glm", "hermes"]


def test_fallback_expansion(make_config) -> None:
    config = make_config(registry=_REGISTRY, default="glm")
    assert select_candidates(config, model="glm") == ["glm", "hermes"]


def test_fallbacks_can_be_disabled(make_config) -> None:
    config = make_config(registry=_REGISTRY, default="glm", fallbacks_enabled=False)
    assert select_candidates(config, model="glm") == ["glm"]


def test_disabled_model_skipped_but_fallback_used(make_config) -> None:
    registry = {
        "glm": {"backend": "mock", "model": "glm", "enabled": False, "fallback": ["hermes"]},
        "hermes": {"backend": "mock", "model": "hermes"},
    }
    config = make_config(registry=registry, default="hermes")
    assert select_candidates(config, model="glm") == ["hermes"]


def test_unknown_model_returns_empty(make_config) -> None:
    config = make_config(registry=_REGISTRY, default="glm")
    assert select_candidates(config, model="ghost") == []


def test_unmatched_tags_fall_back_to_default(make_config) -> None:
    config = make_config(registry=_REGISTRY, default="glm")
    assert select_candidates(config, tags=["nieistniejacy"]) == ["glm", "hermes"]


def test_agent_auto_uses_default(make_config) -> None:
    config = make_config(registry=_REGISTRY, default="glm", agent_models={"chorazy": "auto"})
    assert select_candidates(config, agent="chorazy") == ["glm", "hermes"]
