"""Testy RBAC oraz dostawców sekretów File/SOPS/Vault."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from husarz.config.secrets import (
    FileSecretsProvider,
    SopsSecretsProvider,
    VaultSecretsProvider,
)
from husarz.security import AuthorizationError, Rbac

pytestmark = pytest.mark.unit


# --- RBAC ------------------------------------------------------------------


def test_admin_can_everything() -> None:
    rbac = Rbac()
    assert rbac.can("admin", "tool:shell")
    assert rbac.can("admin", "config:write")


def test_operator_has_tool_wildcard_but_not_config_write() -> None:
    rbac = Rbac()
    assert rbac.can("operator", "tool:shell")  # przez tool:*
    assert rbac.can("operator", "agent:puszkarz")
    assert rbac.can("operator", "config:write") is False


def test_viewer_is_read_only() -> None:
    rbac = Rbac()
    assert rbac.can("viewer", "config:read")
    assert rbac.can("viewer", "tool:shell") is False


def test_unknown_role_denied() -> None:
    assert Rbac().can("ghost", "config:read") is False


def test_require_raises_on_missing_permission() -> None:
    with pytest.raises(AuthorizationError, match="tool:shell"):
        Rbac().require("viewer", "tool:shell")


# --- Dostawcy sekretów -----------------------------------------------------


def test_file_secrets_provider(tmp_path: Path) -> None:
    (tmp_path / "token").write_text("SEKRET\n", encoding="utf-8")
    provider = FileSecretsProvider(tmp_path)
    assert provider.resolve("file:token") == "SEKRET"
    assert provider.resolve("token") == "SEKRET"
    assert provider.resolve("file:brak") is None


def test_file_secrets_provider_is_confined(tmp_path: Path) -> None:
    assert FileSecretsProvider(tmp_path).resolve("file:../../etc/passwd") is None


def test_sops_provider_navigates_key() -> None:
    def fake_decrypt(path: str) -> dict[str, Any]:
        assert path == "secrets.enc.yaml"
        return {"husarz": {"api": {"key": "K123"}}}

    provider = SopsSecretsProvider(decrypt=fake_decrypt)
    assert provider.resolve("sops:secrets.enc.yaml#husarz.api.key") == "K123"
    assert provider.resolve("sops:secrets.enc.yaml#husarz.brak") is None
    assert provider.resolve("sops:brak-fragmentu-klucza") is None


def test_sops_provider_handles_decrypt_error() -> None:
    def bad(path: str) -> dict[str, Any]:
        raise ValueError("błąd sops")

    assert SopsSecretsProvider(decrypt=bad).resolve("sops:x#y") is None


def test_vault_provider_reads_key() -> None:
    def fake_read(path: str) -> dict[str, Any]:
        assert path == "secret/data/husarz"
        return {"token": "T1"}

    provider = VaultSecretsProvider(read=fake_read)
    assert provider.resolve("vault:secret/data/husarz#token") == "T1"
    assert provider.resolve("vault:secret/data/husarz#brak") is None
