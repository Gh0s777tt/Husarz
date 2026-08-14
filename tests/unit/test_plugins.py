"""Testy konektora MCP (odkrywanie narzędzi) — Etap 12b, wszystko OFFLINE.

Transport jest wstrzykiwany (``FakePluginTransport``), więc żaden test nie wykonuje
połączeń sieciowych. Sprawdzamy: kopertę JSON-RPC 2.0 + nagłówek Bearer, normalizację
wyniku, leniwe rozwiązanie tokenu, walidację ``PluginConfig`` i ładowanie z configu.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from husarz.config import load_config
from husarz.config.schema import EgressConfig, PluginConfig
from husarz.plugins import (
    McpClient,
    PluginService,
    RemoteTool,
    build_connector,
    build_plugin_service,
)
from husarz.plugins.errors import (
    PluginDisabledError,
    PluginNotFoundError,
    PluginSecretError,
)
from husarz.ssrf import PinnedTarget

pytestmark = pytest.mark.unit


class FakePluginTransport:
    """Transport testowy: zapisuje ostatnie wywołanie, zwraca kanoniczny wynik tools/list."""

    def __init__(self, result: Any | None = None, status: int = 200) -> None:
        self.calls: list[dict[str, Any]] = []
        self._status = status
        self._result = (
            result
            if result is not None
            else {"tools": [{"name": "echo", "description": "Echo"}, {"name": "add"}]}
        )

    def __call__(
        self,
        target: PinnedTarget,
        headers: dict[str, str],
        json: dict[str, Any],
        timeout: int,
        max_bytes: int,
    ) -> tuple[int, Any]:
        self.calls.append(
            {"url": target.connect_url, "headers": headers, "json": json, "max_bytes": max_bytes}
        )
        return self._status, {"jsonrpc": "2.0", "id": json.get("id"), "result": self._result}


class DictSecrets:
    """Dostawca sekretów oparty o słownik (test)."""

    def __init__(self, values: dict[str, str]) -> None:
        self._values = values

    def resolve(self, ref: str) -> str | None:
        return self._values.get(ref)


def _plugin(**kwargs: Any) -> PluginConfig:
    base: dict[str, Any] = {"name": "p", "endpoint": "http://127.0.0.1:8808/mcp"}
    base.update(kwargs)
    return PluginConfig(**base)


def test_list_tools_builds_jsonrpc_envelope_and_bearer() -> None:
    transport = FakePluginTransport()
    client = McpClient(PinnedTarget.direct("http://127.0.0.1:8808/mcp"), "sekret-abc", transport)
    tools = client.list_tools()
    assert tools == [RemoteTool("echo", "Echo"), RemoteTool("add", "")]
    call = transport.calls[0]
    assert call["json"]["jsonrpc"] == "2.0"
    assert call["json"]["method"] == "tools/list"
    assert call["headers"]["Authorization"] == "Bearer sekret-abc"


def test_list_tools_no_token_omits_authorization() -> None:
    transport = FakePluginTransport()
    McpClient(PinnedTarget.direct("http://127.0.0.1:8808"), "", transport).list_tools()
    assert "Authorization" not in transport.calls[0]["headers"]


def test_list_tools_skips_malformed_entries() -> None:
    transport = FakePluginTransport(result={"tools": [{"description": "brak name"}, "nie-dict", 5]})
    assert McpClient(PinnedTarget.direct("http://127.0.0.1:8808"), "", transport).list_tools() == []


def test_build_connector_uses_plugin_limits() -> None:
    transport = FakePluginTransport()
    client = build_connector(
        _plugin(timeout_seconds=7, max_output_bytes=123), "", EgressConfig(), transport=transport
    )
    client.list_tools()
    assert transport.calls[0]["max_bytes"] == 123


# --- PluginService: token leniwie, wyłączona, nieznana --------------------------


def test_service_resolves_token_lazily() -> None:
    transport = FakePluginTransport()
    service = PluginService(
        {"p": _plugin(token_ref="env:TOK")},
        secrets=DictSecrets({"env:TOK": "tajne-123"}),
        transport=transport,
    )
    service.discover("p")
    assert transport.calls[0]["headers"]["Authorization"] == "Bearer tajne-123"


def test_service_missing_token_raises_secret_error() -> None:
    # Lokalny nierozwiązywalny token_ref → PluginSecretError (nie PluginAuthError zdalne).
    service = PluginService(
        {"p": _plugin(token_ref="env:NIEMA")},
        secrets=DictSecrets({}),
        transport=FakePluginTransport(),
    )
    with pytest.raises(PluginSecretError):
        service.discover("p")


def test_service_disabled_plugin_raises() -> None:
    service = PluginService({"p": _plugin(enabled=False)}, transport=FakePluginTransport())
    with pytest.raises(PluginDisabledError):
        service.discover("p")


def test_service_unknown_plugin_raises() -> None:
    service = PluginService({"p": _plugin()}, transport=FakePluginTransport())
    with pytest.raises(PluginNotFoundError):
        service.discover("inny")


def test_build_plugin_service_none_when_all_disabled() -> None:
    from husarz.config.schema import SecurityConfig

    assert build_plugin_service({"p": _plugin(enabled=False)}, SecurityConfig()) is None
    assert build_plugin_service({"p": _plugin(enabled=True)}, SecurityConfig()) is not None


# --- Walidacja PluginConfig + ładowanie z configu ------------------------------


def test_plugin_config_rejects_raw_token() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        _plugin(token_ref="ghp_surowy_token")


def test_plugin_config_rejects_userinfo_endpoint() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        _plugin(endpoint="http://user:pass@127.0.0.1:8808")


def test_loads_plugins_from_config_dir(write_config, tmp_path: Path) -> None:  # noqa: ANN001
    models = "default: m1\nregistry:\n  m1:\n    backend: mock\n    model: x\n"
    plugin_yaml = "name: local\nendpoint: http://127.0.0.1:9000\nenabled: true\n"
    config_dir = write_config({"models.yaml": models, "plugins/local.yaml": plugin_yaml})
    config = load_config(config_dir)
    assert set(config.plugins) == {"local"}
    assert config.plugins["local"].endpoint == "http://127.0.0.1:9000"


def test_redirect_response_is_error_not_empty_tool_list() -> None:
    """``follow_redirects=False`` (anty-SSRF) sprawia, że 3xx nie jest sukcesem — bez jawnej
    gałęzi 301/302 degradowałoby się do cichego „serwer nie udostępnił narzędzi"."""
    from husarz.plugins.errors import PluginError

    class RedirectTransport:
        def __call__(self, target, headers, json, timeout, max_bytes):  # type: ignore[no-untyped-def]
            return 302, None

    client = McpClient(PinnedTarget.direct("http://127.0.0.1:8808"), "", RedirectTransport())
    with pytest.raises(PluginError, match="przekierowaniem"):
        client.list_tools()
