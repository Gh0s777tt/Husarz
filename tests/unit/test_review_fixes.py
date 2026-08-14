"""Testy regresyjne dla poprawek po adwersaryjnym przeglądzie Etapu 0.

Każdy test odpowiada potwierdzonemu findingowi: correctness loadera, hardening
walidacji per-profil, okno czasowe ROE oraz kontrakt CLI.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from husarz.config import load_config
from husarz.config.errors import ConfigFileNotFoundError, ConfigValidationError
from husarz.config.schema import LogLevel, Profile, RoeConfig, RoeScope, RoeWindow
from husarz.launcher.cli import main

pytestmark = pytest.mark.unit

_MODELS = "default: m1\nregistry:\n  m1:\n    backend: mock\n    model: x\n"


# --------------------------------------------------------------------------
# Loader — nadpisania ENV
# --------------------------------------------------------------------------


def test_unknown_husarz_env_var_is_ignored(minimal_config_dir: Path) -> None:
    """Obca zmienna HUSARZ_* (np. HUSARZ_HOME) nie wywraca startu."""
    env = {"HUSARZ_HOME": "/opt/husarz", "HUSARZ_PLATFORM__PROFILE": "prod"}
    config = load_config(minimal_config_dir, env=env)
    assert config.platform.profile is Profile.PROD


def test_env_preserves_uppercase_map_key(write_config) -> None:
    """Nadpisanie ENV trafia w klucz mapy z wielkimi literami (nie tworzy nowego)."""
    models = "default: GLM_Main\nregistry:\n  GLM_Main:\n    backend: mock\n    model: x\n"
    config_dir = write_config({"models.yaml": models})
    env = {"HUSARZ_MODELS__REGISTRY__GLM_Main__ENABLED": "false"}
    config = load_config(config_dir, env=env)
    assert "GLM_Main" in config.models.registry
    assert "glm_main" not in config.models.registry
    assert config.models.registry["GLM_Main"].enabled is False


def test_env_json_object_coercion(minimal_config_dir: Path) -> None:
    """Wartość ENV w formacie obiektu JSON jest parsowana do mapy."""
    env = {"HUSARZ_ROUTING__COST_CONTROLS": '{"max_tokens_per_request": 1000}'}
    config = load_config(minimal_config_dir, env=env)
    assert config.routing.cost_controls.max_tokens_per_request == 1000


def test_env_malformed_json_falls_back_to_string(minimal_config_dir: Path) -> None:
    """Niepoprawny JSON w nawiasie przechodzi jako string (bez crasha loadera)."""
    env = {"HUSARZ_PLATFORM__LANGUAGE_DEFAULT": "[nie-json"}
    config = load_config(minimal_config_dir, env=env)
    assert config.platform.language_default == "[nie-json"


def test_env_list_replaces_not_merges(write_config) -> None:
    """Lista z warstwy wyższej ZASTĘPUJE listę bazową (nie dopisuje)."""
    config_dir = write_config(
        {
            "models.yaml": _MODELS,
            "security.yaml": "egress:\n  allowlist: [a.example, b.example]\n",
        }
    )
    env = {"HUSARZ_SECURITY__EGRESS__ALLOWLIST": '["c.example"]'}
    config = load_config(config_dir, env=env)
    assert config.security.egress.allowlist == ["c.example"]


def test_precedence_yaml_env_runtime(write_config) -> None:
    """Na jednym kluczu: runtime > ENV > YAML; rodzeństwo z YAML zachowane."""
    config_dir = write_config(
        {"models.yaml": _MODELS, "husarz.yaml": "profile: dev\nlog_level: INFO\n"}
    )
    config = load_config(
        config_dir,
        env={"HUSARZ_PLATFORM__LOG_LEVEL": "ERROR"},
        runtime_overrides={"platform": {"log_level": "WARNING"}},
    )
    assert config.platform.log_level is LogLevel.WARNING
    assert config.platform.profile is Profile.DEV  # deep-merge nie zgubił rodzeństwa


# --------------------------------------------------------------------------
# Loader — lokalizacja katalogu i źródło rejestru modeli
# --------------------------------------------------------------------------


def test_config_dir_resolved_from_env_when_arg_none(minimal_config_dir: Path) -> None:
    """Gdy argument to None, katalog pochodzi z HUSARZ_CONFIG_DIR."""
    config = load_config(None, env={"HUSARZ_CONFIG_DIR": str(minimal_config_dir)})
    assert config.models.default == "m1"


def test_runtime_can_supply_models_without_file(write_config) -> None:
    """Rejestr modeli może przyjść z runtime, gdy brak models.yaml (hierarchia)."""
    config_dir = write_config({"husarz.yaml": "profile: dev\n"})
    config = load_config(
        config_dir,
        runtime_overrides={
            "models": {"default": "m1", "registry": {"m1": {"backend": "mock", "model": "x"}}}
        },
    )
    assert config.models.default == "m1"


def test_missing_models_everywhere_raises(write_config) -> None:
    """Brak modeli w plikach, ENV i runtime = czytelny błąd wskazujący models.yaml."""
    config_dir = write_config({"husarz.yaml": "profile: dev\n"})
    with pytest.raises(ConfigFileNotFoundError, match="models.yaml"):
        load_config(config_dir)


def test_duplicate_numeric_roe_key_raises(write_config) -> None:
    """Dwa pliki ROE o numerycznym engagement_id 42 są wykrywane jako duplikat."""
    entry = "engagement_id: 42\nowner: o\n"
    config_dir = write_config({"models.yaml": _MODELS, "roe/a.yaml": entry, "roe/b.yaml": entry})
    with pytest.raises(ConfigValidationError, match="Zduplikowany klucz '42'"):
        load_config(config_dir)


# --------------------------------------------------------------------------
# Walidacja krzyżowa — narzędzia, routing, profile
# --------------------------------------------------------------------------


def test_agent_tool_validated_even_with_empty_registry(write_config) -> None:
    """Pusty rejestr narzędzi NIE wyłącza walidacji — każde odwołanie to błąd."""
    config_dir = write_config(
        {
            "models.yaml": _MODELS,
            "agents/x.yaml": "name: x\nprompt_file: x.md\nmodel: m1\ntools: [ghost_tool]\n",
        }
    )
    with pytest.raises(ConfigValidationError, match="ghost_tool"):
        load_config(config_dir)


def test_routing_rule_prefers_unknown_model(write_config) -> None:
    """routing.rules[].prefer wskazujący nieistniejący model = błąd krzyżowy."""
    config_dir = write_config(
        {
            "models.yaml": _MODELS,
            "routing.yaml": "rules:\n  - match_tags: [code]\n    prefer: [ghost]\n",
        }
    )
    with pytest.raises(ConfigValidationError, match="ghost"):
        load_config(config_dir)


def test_airgap_forbids_egress_allow_policy(write_config) -> None:
    """Profil airgap z egress.default_policy=allow = błąd (suwerenność danych)."""
    config_dir = write_config(
        {
            "models.yaml": _MODELS,
            "husarz.yaml": "profile: airgap\n",
            "security.yaml": "egress:\n  default_policy: allow\n",
        }
    )
    with pytest.raises(ConfigValidationError, match="airgap"):
        load_config(config_dir)


def test_airgap_rejects_remote_model_endpoint(write_config) -> None:
    """Profil airgap odrzuca model z nielokalnym endpointem."""
    models = (
        "default: m1\nregistry:\n  m1:\n    backend: openai_compat\n"
        "    model: x\n    endpoint: https://api.openai.com/v1\n"
    )
    config_dir = write_config({"models.yaml": models, "husarz.yaml": "profile: airgap\n"})
    with pytest.raises(ConfigValidationError, match="nielokalny endpoint"):
        load_config(config_dir)


def test_airgap_allows_local_model_endpoint(write_config) -> None:
    """Profil airgap dopuszcza lokalny endpoint (localhost)."""
    models = (
        "default: m1\nregistry:\n  m1:\n    backend: openai_compat\n"
        "    model: x\n    endpoint: http://localhost:8000/v1\n"
    )
    config_dir = write_config({"models.yaml": models, "husarz.yaml": "profile: airgap\n"})
    config = load_config(config_dir)
    assert config.platform.profile is Profile.AIRGAP


@pytest.mark.parametrize(
    ("security_yaml", "match"),
    [
        ("sandbox:\n  engine: none\n", "sandbox"),
        ("audit:\n  enabled: false\n", "audytu"),
        ("encryption:\n  at_rest: false\n", "at-rest"),
    ],
)
def test_prod_baseline_forbids_disabling_protections(
    write_config, security_yaml: str, match: str
) -> None:
    """Profil prod nie pozwala cicho wyłączyć sandboxa, audytu ani szyfrowania."""
    config_dir = write_config(
        {"models.yaml": _MODELS, "husarz.yaml": "profile: prod\n", "security.yaml": security_yaml}
    )
    with pytest.raises(ConfigValidationError, match=match):
        load_config(config_dir)


# --------------------------------------------------------------------------
# ROE — bramka aktywności i okno czasowe
# --------------------------------------------------------------------------


def _roe(**overrides) -> RoeConfig:
    base = {
        "engagement_id": "e1",
        "owner": "o",
        "authorized_by": "a",
        "scope": RoeScope(targets_domains=["app.example.local"]),
        "window": RoeWindow(start=datetime(2026, 9, 1, 9), end=datetime(2026, 9, 1, 17)),
    }
    base.update(overrides)
    return RoeConfig(**base)


def test_roe_consent_without_signature_is_inactive() -> None:
    """Zgoda bez podpisu = ROE nieaktywne (bramka nie może być fałszywie pozytywna)."""
    assert _roe(consent=True, signature=None).is_active is False


def test_roe_empty_signature_is_inactive() -> None:
    """Pusty podpis nie aktywuje ROE."""
    assert _roe(consent=True, signature="").is_active is False


def test_roe_is_active_at_respects_window() -> None:
    """is_active_at wymusza okno czasowe."""
    roe = _roe(consent=True, signature="sops:ref")
    assert roe.is_active is True
    assert roe.is_active_at(datetime(2026, 9, 1, 12)) is True
    assert roe.is_active_at(datetime(2026, 9, 1, 8)) is False  # przed oknem
    assert roe.is_active_at(datetime(2026, 9, 1, 18)) is False  # po oknie


def test_roe_inactive_is_never_active_at() -> None:
    """Nieaktywne ROE nie jest aktywne o żadnej porze."""
    roe = _roe(consent=False, signature="sops:ref")
    assert roe.is_active_at(datetime(2026, 9, 1, 12)) is False


# --------------------------------------------------------------------------
# CLI — kontrakt kodów wyjścia i podkomend
# --------------------------------------------------------------------------


def test_up_subcommand_is_wired() -> None:
    """Podkomenda 'up' jest podpięta w parserze (nie uruchamiamy serwera w teście)."""
    from husarz.launcher.cli import _cmd_up, build_parser

    args = build_parser().parse_args(["up"])
    assert args.func is _cmd_up
    # Bez jawnej flagi profil NIE jest nadpisywany — obowiązuje ten z pliku/ENV.
    # Domyślne wstrzykiwanie 'dev' po cichu degradowało konfigurację `profile: prod`,
    # a profil kotwiczy bazową linię bezpieczeństwa (sandbox/audyt/szyfrowanie/podpis ROE).
    assert args.profile is None
    assert build_parser().parse_args(["up", "--profile", "prod"]).profile == "prod"


def test_up_accepts_airgap_profile_from_enum() -> None:
    """Profil 'airgap' (z enuma Profile) jest akceptowany przez parser 'up'."""
    from husarz.launcher.cli import build_parser

    args = build_parser().parse_args(["up", "--profile", "airgap", "--port", "9000"])
    assert args.profile == "airgap"
    assert args.port == 9000


def test_up_rejects_unknown_profile() -> None:
    """Nieznany profil kończy się błędem użycia (SystemExit)."""
    with pytest.raises(SystemExit):
        main(["up", "--profile", "zly"])


def test_no_subcommand_exits_with_usage_error() -> None:
    """Brak podkomendy = SystemExit(2) (błąd użycia, różny od 1 = błąd configu)."""
    with pytest.raises(SystemExit) as exc:
        main([])
    assert exc.value.code == 2


def test_unknown_subcommand_exits() -> None:
    """Nieznana podkomenda = SystemExit."""
    with pytest.raises(SystemExit):
        main(["nieznana-komenda"])
