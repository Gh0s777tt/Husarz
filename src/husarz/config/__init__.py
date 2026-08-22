"""Pakiet konfiguracji Husarza — schematy, loader, dostawcy sekretów.

Publiczne API:
    load_config       — wczytuje i waliduje całą konfigurację,
    HusarzConfig      — zwalidowany model konfiguracji,
    ConfigError i pochodne — hierarchia wyjątków konfiguracji.
"""

from __future__ import annotations

from husarz.config.errors import (
    ConfigError,
    ConfigFileNotFoundError,
    ConfigParseError,
    ConfigValidationError,
)
from husarz.config.loader import load_config, resolve_config_dir
from husarz.config.schema import (
    AgentClass,
    AgentConfig,
    EgressPolicy,
    HusarzConfig,
    ModelBackend,
    ModelSpec,
    Profile,
    RoeConfig,
    SecurityConfig,
    ToolConfig,
)
from husarz.config.secrets import (
    ChainedSecretsProvider,
    EnvSecretsProvider,
    FileSecretsProvider,
    NullSecretsProvider,
    SecretsProvider,
    SopsSecretsProvider,
    VaultSecretsProvider,
    get_secrets_provider,
)

__all__ = [
    "AgentClass",
    "AgentConfig",
    "ConfigError",
    "ConfigFileNotFoundError",
    "ConfigParseError",
    "ConfigValidationError",
    "EgressPolicy",
    "EnvSecretsProvider",
    "FileSecretsProvider",
    "HusarzConfig",
    "ModelBackend",
    "ModelSpec",
    "NullSecretsProvider",
    "Profile",
    "RoeConfig",
    "SecretsProvider",
    "SecurityConfig",
    "SopsSecretsProvider",
    "ToolConfig",
    "VaultSecretsProvider",
    "ChainedSecretsProvider",
    "get_secrets_provider",
    "load_config",
    "resolve_config_dir",
]
