"""Testy wywołania narzędzi wtyczki MCP (``tools/call``) — Etap 13b, wszystko OFFLINE.

Transport jest wstrzykiwany (``FakePluginTransport``) — żaden test nie łączy się z siecią.
Sprawdzamy: kopertę JSON-RPC ``tools/call`` + Bearer, parsowanie NIEZAUFANEGO wyniku,
bramy deny-by-default (``allow_call``/``call_allowlist``/``max_call_bytes``) PRZED egress,
narzędzie ``PluginTool`` (kontrakt „nigdy nie rzuca"), dispatch + walidację configu.
"""

from __future__ import annotations

from typing import Any

import pytest

from husarz.config.schema import EgressConfig, HusarzConfig, PluginConfig
from husarz.plugins import (
    McpClient,
    PluginArgsError,
    PluginCallDeniedError,
    PluginDisabledError,
    PluginService,
    RemoteCallResult,
)
from husarz.plugins.client import _parse_call_result
from husarz.plugins.errors import PluginError, PluginSecretError
from husarz.ssrf import PinnedTarget
from husarz.tools.plugin import PluginTool

pytestmark = pytest.mark.unit


class FakePluginTransport:
    """Transport testowy: zapisuje wywołania, zwraca konfigurowalny wynik ``tools/call``."""

    def __init__(self, *, content: Any = None, is_error: bool = False, status: int = 200) -> None:
        self.calls: list[dict[str, Any]] = []
        self._content = content if content is not None else [{"type": "text", "text": "OK"}]
        self._is_error = is_error
        self._status = status

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
        method = json.get("method")
        if method == "tools/call":
            result: Any = {"content": self._content, "isError": self._is_error}
        else:
            result = {"tools": [{"name": "echo", "description": "Echo"}]}
        return self._status, {"jsonrpc": "2.0", "id": json.get("id"), "result": result}


class DictSecrets:
    def __init__(self, values: dict[str, str]) -> None:
        self._values = values

    def resolve(self, ref: str) -> str | None:
        return self._values.get(ref)


def _plugin(**kw: Any) -> PluginConfig:
    base: dict[str, Any] = {
        "name": "p",
        "endpoint": "http://127.0.0.1:8808/mcp",
        "allow_call": True,
        "call_allowlist": ["echo"],
    }
    base.update(kw)
    return PluginConfig(**base)


def _service(plugin: PluginConfig, transport: FakePluginTransport, **kw: Any) -> PluginService:
    return PluginService({plugin.name: plugin}, transport=transport, egress=EgressConfig(), **kw)


# --- McpClient.call_tool + _parse_call_result --------------------------------


def test_call_tool_builds_jsonrpc_envelope_and_bearer() -> None:
    transport = FakePluginTransport(content=[{"type": "text", "text": "wynik"}])
    client = McpClient(PinnedTarget.direct("http://127.0.0.1:8808"), "sekret-xyz", transport)
    result = client.call_tool("echo", {"msg": "hej"})
    assert result == RemoteCallResult(text="wynik", is_error=False)
    envelope = transport.calls[0]["json"]
    assert envelope["method"] == "tools/call"
    assert envelope["params"] == {"name": "echo", "arguments": {"msg": "hej"}}
    assert transport.calls[0]["headers"]["Authorization"] == "Bearer sekret-xyz"


def test_call_tool_is_error_maps_to_result_flag() -> None:
    transport = FakePluginTransport(content=[{"type": "text", "text": "boom"}], is_error=True)
    result = McpClient(PinnedTarget.direct("http://127.0.0.1:8808"), "", transport).call_tool(
        "echo", {}
    )
    assert result.is_error is True and result.text == "boom"


def test_call_tool_jsonrpc_error_raises_plugin_error() -> None:
    class ErrTransport(FakePluginTransport):
        def __call__(self, target, headers, json, timeout, max_bytes):  # type: ignore[no-untyped-def]
            self.calls.append({"json": json})
            return 200, {"jsonrpc": "2.0", "id": json.get("id"), "error": {"code": -32000}}

    with pytest.raises(PluginError):
        McpClient(PinnedTarget.direct("http://127.0.0.1:8808"), "", ErrTransport()).call_tool(
            "echo", {}
        )


def test_parse_call_result_shapes() -> None:
    # nie-dict → fail-safe błąd; brak content → pusty; mieszane bloki → text + placeholder.
    assert _parse_call_result("nie-dict", max_bytes=100) == RemoteCallResult(text="", is_error=True)
    assert _parse_call_result({}, max_bytes=100) == RemoteCallResult(text="", is_error=False)
    mixed = {
        "content": [
            {"type": "text", "text": "a"},
            {"type": "image", "data": "AAAA"},
            "nie-dict",
            {"type": "text", "text": "b"},
        ]
    }
    parsed = _parse_call_result(mixed, max_bytes=100)
    assert parsed.text == "a\n[pominięto blok typu 'image']\nb"  # bajty binarne NIGDY nie pobrane


def test_parse_call_result_caps_text_by_bytes() -> None:
    big = {"content": [{"type": "text", "text": "x" * 500}]}
    assert len(_parse_call_result(big, max_bytes=50).text.encode("utf-8")) <= 50


# --- PluginService.call — bramy deny-by-default ------------------------------


def test_call_allow_call_false_denied_before_egress() -> None:
    transport = FakePluginTransport()
    service = _service(_plugin(allow_call=False, call_allowlist=[]), transport)
    with pytest.raises(PluginCallDeniedError):
        service.call("p", "echo", {})
    assert transport.calls == []  # transport NIETKNIĘTY (odmowa przed siecią)


def test_call_tool_outside_allowlist_denied_before_egress() -> None:
    transport = FakePluginTransport()
    service = _service(_plugin(call_allowlist=["inne"]), transport)
    with pytest.raises(PluginCallDeniedError):
        service.call("p", "echo", {})
    assert transport.calls == []


def test_call_disabled_plugin_raises() -> None:
    transport = FakePluginTransport()
    service = _service(_plugin(enabled=False), transport)
    with pytest.raises(PluginDisabledError):
        service.call("p", "echo", {})


def test_call_unresolvable_token_fails_before_egress() -> None:
    transport = FakePluginTransport()
    plugin = _plugin(token_ref="env:BRAK")
    service = _service(plugin, transport, secrets=DictSecrets({}))
    with pytest.raises(PluginSecretError):
        service.call("p", "echo", {})
    assert transport.calls == []  # sekret nierozwiązywalny → brak wyjścia na sieć


def test_call_args_too_large_raises_before_egress() -> None:
    transport = FakePluginTransport()
    service = _service(_plugin(max_call_bytes=32), transport)
    with pytest.raises(PluginArgsError):
        service.call("p", "echo", {"payload": "x" * 200})
    assert transport.calls == []


def test_call_happy_path_reaches_transport() -> None:
    transport = FakePluginTransport(content=[{"type": "text", "text": "pong"}])
    result = _service(_plugin(), transport).call("p", "echo", {"a": 1})
    assert result.text == "pong" and result.is_error is False
    assert len(transport.calls) == 1


# --- PluginTool — kontrakt „nigdy nie rzuca" ---------------------------------


def test_plugin_tool_call_ok_and_is_error() -> None:
    ok = PluginTool(
        "plugin_p", "p", _service(_plugin(), FakePluginTransport()), max_output_bytes=1000
    )
    res = ok.call("echo", {})
    assert res.ok is True and res.output == "OK"
    err_tool = PluginTool(
        "plugin_p",
        "p",
        _service(_plugin(), FakePluginTransport(is_error=True)),
        max_output_bytes=1000,
    )
    assert err_tool.call("echo", {}).ok is False  # isError → ok=False


def test_plugin_tool_none_service_degrades() -> None:
    tool = PluginTool("plugin_p", "p", None, max_output_bytes=1000)
    assert tool.call("echo", {}).ok is False
    assert tool.list().ok is False


def test_plugin_tool_denied_degrades_to_ok_false() -> None:
    tool = PluginTool(
        "plugin_p",
        "p",
        _service(_plugin(allow_call=False, call_allowlist=[]), FakePluginTransport()),
        max_output_bytes=1000,
    )
    assert tool.call("echo", {}).ok is False  # PluginCallDeniedError złapane, nie wyjątek


def test_plugin_tool_list_renders_untrusted_names() -> None:
    tool = PluginTool(
        "plugin_p", "p", _service(_plugin(), FakePluginTransport()), max_output_bytes=1000
    )
    res = tool.list()
    assert res.ok is True and "echo" in res.output


# --- Dispatch: akcje list/call ----------------------------------------------


def test_dispatch_plugin_actions() -> None:
    from husarz.tools.dispatch import ToolDispatcher

    tool = PluginTool(
        "plugin_p", "p", _service(_plugin(), FakePluginTransport()), max_output_bytes=1000
    )
    disp = ToolDispatcher({"plugin_p": tool}, {"plugin_p": "plugin"})
    assert disp.dispatch("plugin_p", "call", {"name": "echo", "arguments": {}}).ok is True
    assert disp.dispatch("plugin_p", "list", {}).ok is True
    assert disp.dispatch("plugin_p", "call", {}).ok is False  # brak 'name'
    assert disp.dispatch("plugin_p", "call", {"name": "echo", "arguments": "zla"}).ok is False
    manual = disp.manual(["plugin_p"])
    assert "call" in manual and "list" in manual


# --- Loader + schema ---------------------------------------------------------


def test_loader_builds_plugin_tool_and_degrades_without_service(tmp_path: Any) -> None:
    from husarz.config.schema import ToolConfig
    from husarz.tools.loader import build_tools

    cfg = HusarzConfig(
        models={"default": "m", "registry": {"m": {"backend": "mock", "model": "x"}}},
        plugins={"srv": _plugin(name="srv")},
        tools={"plugin_x": ToolConfig(name="plugin_x", kind="plugin", config={"plugin": "srv"})},
    )
    # Bez plugin_service → narzędzie się BUDUJE, ale call/list degradują do ok=False.
    tools = build_tools(cfg, workspace=tmp_path)
    assert "plugin_x" in tools
    assert isinstance(tools["plugin_x"], PluginTool)


def test_schema_allow_call_requires_allowlist() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        PluginConfig(name="p", endpoint="http://127.0.0.1:8808", allow_call=True, call_allowlist=[])
    with pytest.raises(ValidationError):  # duplikaty
        PluginConfig(name="p", endpoint="http://127.0.0.1:8808", call_allowlist=["a", "a"])


def test_cross_validate_plugin_tool_requires_existing_connector() -> None:
    from husarz.config.schema import ToolConfig

    with pytest.raises(ValueError, match="nieznanej wtyczki"):
        HusarzConfig(
            models={"default": "m", "registry": {"m": {"backend": "mock", "model": "x"}}},
            tools={
                "plugin_x": ToolConfig(name="plugin_x", kind="plugin", config={"plugin": "brak"})
            },
        )


def test_arg_summary_hashes_arguments_even_when_not_dict() -> None:
    # M1 (regresja z przeglądu): 'arguments' ZAWSZE {bytes, sha256} — także gdy model poda
    # STRING zamiast mapy (inaczej surowa treść wpadłaby do gałęzi generycznej i wyciekła).
    from husarz.agents.tool_loop import _arg_summary

    out = _arg_summary({"name": "search", "arguments": "SEKRET-PII-1234567890"})
    assert set(out["arguments"]) == {"bytes", "sha256"}
    assert "SEKRET" not in str(out["arguments"])  # surowa treść NIE trafia do audytu
    assert set(_arg_summary({"arguments": {"q": "x"}})["arguments"]) == {"bytes", "sha256"}


def test_call_allowlist_whitespace_normalized() -> None:
    # Wpisy z białymi znakami przycinane → runtime dopasowuje czystą nazwę (brak cichej odmowy).
    cfg = PluginConfig(
        name="p", endpoint="http://127.0.0.1:8808", allow_call=True, call_allowlist=[" echo "]
    )
    assert cfg.call_allowlist == ["echo"]
