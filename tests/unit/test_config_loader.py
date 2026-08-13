"""Testy loadera konfiguracji: wczytywanie, wymagane pliki, błędy składni."""

from __future__ import annotations

from pathlib import Path

import pytest

from husarz.config import HusarzConfig, load_config
from husarz.config.errors import (
    ConfigFileNotFoundError,
    ConfigParseError,
    ConfigValidationError,
)
from husarz.config.schema import Profile

pytestmark = pytest.mark.unit


def test_repo_example_config_validates(repo_config_dir: Path) -> None:
    """Przykładowa konfiguracja repo musi walidować się out-of-the-box (profil dev)."""
    config = load_config(repo_config_dir)
    assert isinstance(config, HusarzConfig)
    assert config.platform.profile is Profile.DEV
    assert config.models.default == "glm-main"
    # Wszyscy agenci ze specyfikacji obecni.
    assert set(config.agents) == {
        "husarz",
        "bielik",
        "kopijnik",
        "zwiadowca",
        "puszkarz",
        "kanclerz",
        "chorazy",
    }
    # Puszkarz wymaga ROE.
    assert config.agents["puszkarz"].roe_required is True


def test_minimal_config_loads(minimal_config_dir: Path) -> None:
    """Najmniejsza konfiguracja (samo models.yaml) wczytuje się z domyślnymi."""
    config = load_config(minimal_config_dir)
    assert config.models.default == "m1"
    # Domyślne z Pydantic — nic nie zaszyte w loaderze.
    assert config.platform.profile is Profile.DEV
    assert config.security.egress.default_policy.value == "deny"
    assert config.agents == {}


def test_missing_config_dir_raises(tmp_path: Path) -> None:
    """Brak katalogu konfiguracji = czytelny wyjątek, nie crash."""
    with pytest.raises(ConfigFileNotFoundError):
        load_config(tmp_path / "nie-istnieje")


def test_missing_required_models_yaml_raises(write_config) -> None:
    """Brak wymaganego models.yaml = czytelny wyjątek."""
    config_dir = write_config({"husarz.yaml": "profile: dev\n"})
    with pytest.raises(ConfigFileNotFoundError, match="models.yaml"):
        load_config(config_dir)


def test_invalid_yaml_syntax_raises(write_config) -> None:
    """Błąd składni YAML = ConfigParseError z nazwą pliku."""
    config_dir = write_config({"models.yaml": "default: m1\n  bad: : indent\n"})
    with pytest.raises(ConfigParseError):
        load_config(config_dir)


def test_non_mapping_yaml_raises(write_config) -> None:
    """Plik, który nie jest mapą na najwyższym poziomie, daje czytelny błąd."""
    config_dir = write_config({"models.yaml": "- a\n- b\n"})
    with pytest.raises(ConfigParseError, match="mapę"):
        load_config(config_dir)


def test_duplicate_multi_key_raises(write_config, minimal_config_dir: Path) -> None:
    """Dwa pliki agentów o tej samej nazwie = błąd (klucze unikalne)."""
    agent = "name: dup\nagent_class: towarzysz\nprompt_file: x.md\nmodel: m1\n"
    config_dir = write_config(
        {
            "models.yaml": (minimal_config_dir / "models.yaml").read_text(encoding="utf-8"),
            "agents/a.yaml": agent,
            "agents/b.yaml": agent,
        }
    )
    with pytest.raises(ConfigValidationError, match="Zduplikowany klucz"):
        load_config(config_dir)
