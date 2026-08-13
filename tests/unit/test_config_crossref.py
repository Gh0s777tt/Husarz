"""Testy walidacji krzyżowej: referencje modeli/narzędzi oraz reguły profilu airgap."""

from __future__ import annotations

import pytest

from husarz.config import load_config
from husarz.config.errors import ConfigValidationError

pytestmark = pytest.mark.unit

_MODELS = "default: m1\nregistry:\n  m1:\n    backend: mock\n    model: x\n"


def test_routing_references_unknown_model(write_config) -> None:
    """routing.agent_models wskazujący nieistniejący model = błąd krzyżowy."""
    config_dir = write_config(
        {
            "models.yaml": _MODELS,
            "routing.yaml": "agent_models:\n  husarz: ghost\n",
        }
    )
    with pytest.raises(ConfigValidationError, match="ghost"):
        load_config(config_dir)


def test_agent_references_unknown_model(write_config) -> None:
    """Agent wskazujący nieistniejący model = błąd krzyżowy."""
    config_dir = write_config(
        {
            "models.yaml": _MODELS,
            "agents/x.yaml": "name: x\nprompt_file: x.md\nmodel: ghost\n",
        }
    )
    with pytest.raises(ConfigValidationError, match="ghost"):
        load_config(config_dir)


def test_agent_references_unknown_tool(write_config) -> None:
    """Agent odwołujący się do niezdefiniowanego narzędzia = błąd krzyżowy."""
    config_dir = write_config(
        {
            "models.yaml": _MODELS,
            "agents/x.yaml": "name: x\nprompt_file: x.md\nmodel: m1\ntools: [ghost_tool]\n",
            "tools/shell.yaml": "name: shell\nkind: shell\n",
        }
    )
    with pytest.raises(ConfigValidationError, match="ghost_tool"):
        load_config(config_dir)


def test_agent_auto_model_is_allowed(write_config) -> None:
    """model: auto jest dozwolony (decyduje router)."""
    config_dir = write_config(
        {
            "models.yaml": _MODELS,
            "agents/x.yaml": "name: x\nprompt_file: x.md\nmodel: auto\n",
        }
    )
    config = load_config(config_dir)
    assert config.agents["x"].model == "auto"


def test_airgap_forbids_egress_allowlist(write_config) -> None:
    """Profil airgap z niepustą allowlistą egress = błąd."""
    config_dir = write_config(
        {
            "models.yaml": _MODELS,
            "husarz.yaml": "profile: airgap\n",
            "security.yaml": "egress:\n  default_policy: deny\n  allowlist: [example.com]\n",
        }
    )
    with pytest.raises(ConfigValidationError, match="airgap"):
        load_config(config_dir)


def test_airgap_forbids_sandbox_network(write_config) -> None:
    """Profil airgap z siecią w sandboxie = błąd."""
    config_dir = write_config(
        {
            "models.yaml": _MODELS,
            "husarz.yaml": "profile: airgap\n",
            "security.yaml": "sandbox:\n  network: true\n",
        }
    )
    with pytest.raises(ConfigValidationError, match="airgap"):
        load_config(config_dir)


def test_airgap_valid_config_loads(write_config) -> None:
    """Poprawny profil airgap (deny, brak sieci) wczytuje się bez błędu."""
    config_dir = write_config(
        {
            "models.yaml": _MODELS,
            "husarz.yaml": "profile: airgap\n",
        }
    )
    config = load_config(config_dir)
    assert config.platform.profile.value == "airgap"
    assert config.security.egress.allowlist == []
