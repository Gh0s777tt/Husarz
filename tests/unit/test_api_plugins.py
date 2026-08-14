"""Testy API wtyczek: lista (bez sekretu), odkrywanie narzędzi, RBAC, egress, audyt.

Wszystko przez ``TestClient`` z WSTRZYKNIĘTYM transportem — bez sieci.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from husarz.api import create_app
from husarz.config import load_config
from husarz.config.schema import EgressConfig, PluginConfig
from husarz.plugins import PluginService
from husarz.security import AuditLog
from husarz.ssrf import PinnedTarget

pytestmark = pytest.mark.unit


class FakePluginTransport:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(
        self,
        target: PinnedTarget,
        headers: dict[str, str],
        json: dict[str, Any],
        timeout: int,
        max_bytes: int,
    ) -> tuple[int, Any]:
        self.calls += 1
        return 200, {
            "jsonrpc": "2.0",
            "id": json.get("id"),
            "result": {"tools": [{"name": "echo", "description": "Echo"}]},
        }


class DictSecrets:
    def __init__(self, values: dict[str, str]) -> None:
        self._values = values

    def resolve(self, ref: str) -> str | None:
        return self._values.get(ref)


def _plugin(**kw: Any) -> PluginConfig:
    base: dict[str, Any] = {"name": "local", "endpoint": "http://127.0.0.1:8808/mcp"}
    base.update(kw)
    return PluginConfig(**base)


def _service(transport: Any = None, **plugin_kw: Any) -> PluginService:
    return PluginService(
        {"local": _plugin(**plugin_kw)},
        secrets=DictSecrets({"env:TOK": "tajne-xyz"}),
        egress=EgressConfig(),
        transport=transport or FakePluginTransport(),
    )


def _client(config_dir: Path, service: PluginService | None, **kw: Any) -> TestClient:
    config = load_config(config_dir)
    audit = kw.pop("audit", AuditLog())
    return TestClient(create_app(config, audit=audit, plugin_service=service, **kw))


def test_plugins_404_when_disabled(repo_config_dir: Path) -> None:
    client = _client(repo_config_dir, None)  # brak plugin_service
    assert client.get("/api/plugins").status_code == 404


def test_list_plugins_hides_secret(repo_config_dir: Path) -> None:
    client = _client(repo_config_dir, _service(token_ref="env:TOK"))
    resp = client.get("/api/plugins")
    assert resp.status_code == 200
    body = resp.json()
    assert body[0]["name"] == "local"
    assert body[0]["token_ref"] == "env:TOK"  # tylko referencja
    assert "tajne-xyz" not in resp.text  # sama wartość sekretu NIE wycieka


def test_discover_returns_remote_tools(repo_config_dir: Path) -> None:
    client = _client(repo_config_dir, _service(token_ref="env:TOK"))
    resp = client.get("/api/plugins/local/tools")
    assert resp.status_code == 200
    assert resp.json() == [{"name": "echo", "description": "Echo"}]


def test_discover_audited_without_token(repo_config_dir: Path) -> None:
    audit = AuditLog()
    client = _client(repo_config_dir, _service(token_ref="env:TOK"), audit=audit)
    client.get("/api/plugins/local/tools")
    actions = [e.action for e in audit.entries]
    assert "plugin.discover" in actions
    # Token/Authorization NIE mogą pojawić się w niemodyfikowalnym audycie.
    blob = repr([e.detail for e in audit.entries])
    assert "tajne-xyz" not in blob and "Authorization" not in blob
    assert audit.verify() is True


def test_discover_unknown_plugin_404(repo_config_dir: Path) -> None:
    client = _client(repo_config_dir, _service())
    assert client.get("/api/plugins/inny/tools").status_code == 404


def test_discover_blocked_endpoint_returns_403(repo_config_dir: Path) -> None:
    transport = FakePluginTransport()
    service = PluginService(
        {"evil": PluginConfig(name="evil", endpoint="http://169.254.169.254/mcp")},
        egress=EgressConfig(),
        transport=transport,
    )
    client = _client(repo_config_dir, service)
    assert client.get("/api/plugins/evil/tools").status_code == 403
    assert transport.calls == 0  # SSRF: brak wyjścia na sieć


def test_rbac_user_cannot_read_plugins(repo_config_dir: Path) -> None:
    # Rola 'user' nie ma plugin:read → 403 (przy włączonym uwierzytelnianiu).
    client = _client(repo_config_dir, _service(), api_token="s3cret", api_role="user")
    resp = client.get("/api/plugins", headers={"Authorization": "Bearer s3cret"})
    assert resp.status_code == 403


def test_unresolved_token_returns_500_not_502(repo_config_dir: Path) -> None:
    # Lokalny nierozwiązywalny token_ref → 500 (błąd konfiguracji), NIE 502 (wina serwera).
    service = PluginService(
        {"local": _plugin(token_ref="env:NIEMA")},
        secrets=DictSecrets({}),  # brak sekretu
        egress=EgressConfig(),
        transport=FakePluginTransport(),
    )
    client = _client(repo_config_dir, service)
    resp = client.get("/api/plugins/local/tools")
    assert resp.status_code == 500
    assert "sekret" in resp.json()["detail"].lower()
