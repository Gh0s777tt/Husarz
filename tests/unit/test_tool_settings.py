"""Typowane ustawienia narzędzi per ``kind`` (Etap 3b) — walidacja przy STARCIE.

``ToolConfig.config`` było nietypowaną mapą, sprawdzaną dopiero (i tylko częściowo) przy
budowie narzędzia. Skutek był gorszy niż literówki: dostarczana konfiguracja zawierała
klucze, których NIKT nie czytał, w tym takie wyglądające jak kontrole bezpieczeństwa —
`shell.network`, `cpu_limit`, `memory_limit`. Operator ustawiający `network: false` mógł
sądzić, że wyłączył sieć narzędziu, podczas gdy jedynym realnym sterowaniem jest
``security.sandbox``. Testy pilnują, że taki cichy no-op jest teraz błędem startu.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from husarz.config.errors import ConfigError
from husarz.config.schema import (
    FileEditSettings,
    GitToolSettings,
    RunTestsSettings,
    ToolConfig,
    WebToolSettings,
)

pytestmark = pytest.mark.unit


def _tool(kind: str, config: dict[str, Any]) -> ToolConfig:
    return ToolConfig(name=f"t-{kind}", kind=kind, config=config)


# --- Literówki i martwe klucze = błąd startu --------------------------------


@pytest.mark.parametrize(
    ("kind", "config"),
    [
        ("web", {"max_byte": 10}),  # literówka
        ("web", {"method": "GET"}),  # klucz, którego nikt nie czytał
        ("file_edit", {"root": "/workspace"}),  # konfinacja idzie z platform.workspace_dir
        ("git", {"workdir": "/workspace"}),
        ("run_tests", {"timeout_seconds": 300}),  # limit czasu → security.sandbox
        ("shell", {"network": False}),  # WYGLĄDAŁO jak kontrola bezpieczeństwa
        ("shell", {"cpu_limit": "1"}),
        ("shell", {"memory_limit": "512m"}),
        ("plugin", {"plugin": "x", "nieznany": 1}),
    ],
)
def test_unknown_setting_is_rejected_at_startup(kind: str, config: dict[str, Any]) -> None:
    with pytest.raises(ValueError, match="config"):
        _tool(kind, config)


def test_error_message_names_tool_and_kind() -> None:
    """Komunikat ma prowadzić operatora do pliku, nie zmuszać do zgadywania."""
    with pytest.raises(ValueError) as excinfo:
        _tool("web", {"method": "GET"})
    message = str(excinfo.value)
    assert "t-web" in message and "web" in message and "method" in message


def test_wrong_type_is_rejected() -> None:
    with pytest.raises(ValueError):
        _tool("web", {"max_bytes": "dużo"})


def test_out_of_range_value_is_rejected() -> None:
    with pytest.raises(ValueError):
        _tool("web", {"max_bytes": 0})  # ge=1


# --- Poprawne ustawienia i wartości domyślne --------------------------------


def test_defaults_apply_when_config_empty() -> None:
    settings = _tool("web", {}).settings_as(WebToolSettings)
    assert settings.max_bytes == 2_000_000 and settings.timeout_seconds == 20


def test_null_value_falls_back_to_default() -> None:
    """``klucz: null`` w YAML znaczy „nie ustawiam" — zachowanie sprzed typowania."""
    settings = _tool("web", {"max_bytes": None}).settings_as(WebToolSettings)
    assert settings.max_bytes == 2_000_000


def test_typed_values_are_available_to_builders() -> None:
    assert _tool("git", {"allow_push": True}).settings_as(GitToolSettings).allow_push is True
    assert _tool("run_tests", {"command": "pytest -x"}).settings_as(RunTestsSettings).command == (
        "pytest -x"
    )
    file_edit = _tool("file_edit", {"deny_globs": ["**/*.key"]}).settings_as(FileEditSettings)
    assert file_edit.deny_globs == ["**/*.key"]


def test_shell_has_no_own_settings() -> None:
    """Izolacja `shell` ma JEDNO źródło prawdy — `security.sandbox`, nie sekcja narzędzia."""
    from husarz.config.schema import ShellSettings

    assert _tool("shell", {}).settings_as(ShellSettings) is not None
    assert not ShellSettings.model_fields


def test_plugin_requires_connector_name() -> None:
    with pytest.raises(ValueError):
        _tool("plugin", {})  # brak wymaganego `plugin`


def test_settings_as_rejects_mismatched_model() -> None:
    """Ochrona przed błędem programisty: builder pobierający nie ten model ma paść głośno."""
    with pytest.raises(ValueError, match="nie są typu"):
        _tool("web", {}).settings_as(GitToolSettings)


def test_unknown_kind_defers_error_to_build_tools() -> None:
    """Nieznany rodzaj nie jest błędem SCHEMATU — zgłasza go ``build_tools`` (bez zmian)."""
    assert _tool("nieznany", {"cokolwiek": 1}).kind == "nieznany"


# --- Konfiguracja repo i loader --------------------------------------------


def test_repo_config_has_no_dead_tool_settings(repo_config_dir: Path) -> None:
    """Dostarczana konfiguracja MUSI przechodzić walidację — inaczej ktoś ją skopiuje
    razem z martwymi kluczami i uzna, że coś ustawił."""
    from husarz.config import load_config

    config = load_config(repo_config_dir)
    assert set(config.tools) >= {"web", "shell", "git", "file_edit", "run_tests", "rag"}


def test_loader_skips_appledouble_sidecars(write_config, tmp_path: Path) -> None:  # noqa: ANN001
    """macOS na exFAT/NTFS tworzy `._<nazwa>.yaml` z metadanymi — binarny sidecar nie może
    wywracać startu (a wcześniej dawał surowy UnicodeDecodeError, nie ConfigError)."""
    from husarz.config import load_config

    config_dir = write_config(
        {
            "models.yaml": "default: m1\nregistry:\n  m1:\n    backend: mock\n    model: x\n",
            "tools/web.yaml": "name: web\nkind: web\nconfig:\n  max_bytes: 123\n",
        }
    )
    (config_dir / "tools" / "._web.yaml").write_bytes(b"\x00\xb0\xff binarny sidecar")
    config = load_config(config_dir)
    assert config.tools["web"].settings_as(WebToolSettings).max_bytes == 123


def test_non_utf8_config_file_gives_readable_error(write_config) -> None:  # noqa: ANN001
    """Plik nie-UTF-8 (nie sidecar) → czytelny ``ConfigError``, nie surowy wyjątek kodeka."""
    from husarz.config import load_config

    config_dir = write_config(
        {"models.yaml": "default: m1\nregistry:\n  m1:\n    backend: mock\n    model: x\n"}
    )
    (config_dir / "tools").mkdir(exist_ok=True)
    (config_dir / "tools" / "zly.yaml").write_bytes(b"name: web\nkind: web\n\xb0\xff")
    with pytest.raises(ConfigError, match="UTF-8"):
        load_config(config_dir)
