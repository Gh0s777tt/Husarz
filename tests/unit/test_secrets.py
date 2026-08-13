"""Testy dostawców sekretów."""

from __future__ import annotations

import pytest

from husarz.config.schema import SecretsProviderKind
from husarz.config.secrets import (
    EnvSecretsProvider,
    NullSecretsProvider,
    get_secrets_provider,
)

pytestmark = pytest.mark.unit


def test_null_provider_returns_none() -> None:
    assert NullSecretsProvider().resolve("cokolwiek") is None


def test_env_provider_resolves(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HUSARZ_TEST_SECRET", "wartosc")
    provider = EnvSecretsProvider()
    assert provider.resolve("env:HUSARZ_TEST_SECRET") == "wartosc"
    assert provider.resolve("HUSARZ_TEST_SECRET") == "wartosc"
    assert provider.resolve("env:NIE_ISTNIEJE") is None


def test_factory_none_and_env() -> None:
    assert isinstance(get_secrets_provider(SecretsProviderKind.NONE), NullSecretsProvider)
    assert isinstance(get_secrets_provider(SecretsProviderKind.ENV), EnvSecretsProvider)


@pytest.mark.parametrize("kind", [SecretsProviderKind.VAULT, SecretsProviderKind.SOPS])
def test_factory_vault_sops_require_explicit_construction(kind: SecretsProviderKind) -> None:
    """Vault/SOPS wymagają jawnej konstrukcji z parametrami (nie przez fabrykę kind)."""
    with pytest.raises(ValueError, match="jawnej konstrukcji"):
        get_secrets_provider(kind)
