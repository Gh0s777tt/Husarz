"""Integracja: nadpisanie runtime propaguje politykę wtyczek do serwisu — 0 sieci.

Regresja na fail-open kill-switcha (przegląd 13b): `plugin_service` musi być PRZEBUDOWANY
z bieżącego configu przy `POST /api/config/runtime` (przez ``plugin_service_factory``), tak
by zmiany polityki konektora (enabled/allow_call/call_allowlist/egress) realnie obowiązywały.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from husarz.api import create_app
from husarz.config import load_config
from husarz.plugins import build_plugin_service

pytestmark = pytest.mark.integration


class FakePluginTransport:
    def __call__(self, target, headers, json, timeout, max_bytes):  # type: ignore[no-untyped-def]
        return 200, {"jsonrpc": "2.0", "id": json.get("id"), "result": {"tools": []}}


def test_plugin_service_rebuilt_on_runtime_override(repo_config_dir: Path) -> None:
    config = load_config(repo_config_dir)

    def factory(cfg: Any) -> Any:
        return build_plugin_service(cfg.plugins, cfg.security, transport=FakePluginTransport())

    client = TestClient(
        create_app(config, config_dir=repo_config_dir, plugin_service_factory=factory)
    )
    # Start: example-mcp wyłączona → żadna wtyczka włączona → serwis None → 404.
    assert client.get("/api/plugins").status_code == 404
    # Nadpisanie runtime włącza wtyczkę.
    resp = client.post(
        "/api/config/runtime",
        json={"overrides": {"plugins": {"example-mcp": {"enabled": True}}}},
    )
    assert resp.status_code == 200 and resp.json()["ok"] is True
    # Serwis PRZEBUDOWANY z nowego configu — bez tego byłoby dalej 404 (fail-open kill-switch).
    listing = client.get("/api/plugins")
    assert listing.status_code == 200
    assert "example-mcp" in [p["name"] for p in listing.json()]
