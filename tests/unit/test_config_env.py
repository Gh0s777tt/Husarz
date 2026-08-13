"""Testy hierarchii nadpisań: ENV (HUSARZ_*) i runtime_overrides."""

from __future__ import annotations

from pathlib import Path

import pytest

from husarz.config import load_config
from husarz.config.schema import LogLevel, Profile

pytestmark = pytest.mark.unit


def test_env_overrides_scalar(minimal_config_dir: Path) -> None:
    """ENV nadpisuje wartość skalarną (platform.profile)."""
    env = {"HUSARZ_PLATFORM__PROFILE": "prod", "HUSARZ_PLATFORM__LOG_LEVEL": "DEBUG"}
    config = load_config(minimal_config_dir, env=env)
    assert config.platform.profile is Profile.PROD
    assert config.platform.log_level is LogLevel.DEBUG


def test_env_overrides_yaml_value(write_config) -> None:
    """ENV ma wyższy priorytet niż wartość z pliku YAML."""
    config_dir = write_config(
        {
            "models.yaml": "default: m1\nregistry:\n  m1:\n    backend: mock\n    model: x\n",
            "husarz.yaml": "profile: dev\nlog_level: INFO\n",
        }
    )
    env = {"HUSARZ_PLATFORM__LOG_LEVEL": "ERROR"}
    config = load_config(config_dir, env=env)
    assert config.platform.log_level is LogLevel.ERROR


def test_env_json_list_coercion(minimal_config_dir: Path) -> None:
    """Wartość ENV w formacie JSON (lista) jest poprawnie parsowana."""
    env = {"HUSARZ_SECURITY__EGRESS__ALLOWLIST": '["docs.python.org", "pypi.org"]'}
    config = load_config(minimal_config_dir, env=env)
    assert config.security.egress.allowlist == ["docs.python.org", "pypi.org"]


def test_runtime_overrides_have_highest_priority(minimal_config_dir: Path) -> None:
    """runtime_overrides (panel) przebijają ENV."""
    env = {"HUSARZ_PLATFORM__LOG_LEVEL": "ERROR"}
    config = load_config(
        minimal_config_dir,
        env=env,
        runtime_overrides={"platform": {"log_level": "WARNING"}},
    )
    assert config.platform.log_level is LogLevel.WARNING


def test_config_dir_env_is_not_treated_as_override(minimal_config_dir: Path) -> None:
    """HUSARZ_CONFIG_DIR steruje lokalizacją, nie treścią — nie trafia do configu."""
    env = {"HUSARZ_CONFIG_DIR": "/inny/kat", "HUSARZ_PLATFORM__PROFILE": "airgap"}
    # config_dir podany jawnie ma priorytet nad ENV; airgap wymaga deny (spełnione domyślnie).
    config = load_config(minimal_config_dir, env=env)
    assert config.platform.profile is Profile.AIRGAP


def test_env_without_prefix_ignored(minimal_config_dir: Path) -> None:
    """Zmienne bez prefiksu HUSARZ_ są ignorowane."""
    env = {"PROFILE": "prod", "PATH": "/usr/bin"}
    config = load_config(minimal_config_dir, env=env)
    assert config.platform.profile is Profile.DEV
