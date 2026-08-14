"""Niezmienniki bezpieczeństwa wywołania wtyczki MCP (``tools/call``) — Etap 13b.

Skupienie: deny-by-default (allow_call/call_allowlist PRZED egress), SSRF re-walidowany
per wywołanie, airgap-gate startowy (loopback), brak eksfiltracji sekretów przez ``arguments``,
token poza URL, degradacja transportu do ``ok=False``. Wszystko OFFLINE (wstrzyknięty transport).
"""

from __future__ import annotations

from typing import Any

import pytest

from husarz.config.schema import EgressConfig, HusarzConfig, PluginConfig
from husarz.plugins import PluginService
from husarz.plugins.errors import PluginTransportError
from husarz.router.egress import EgressError
from husarz.tools.plugin import PluginTool

pytestmark = pytest.mark.security

_MODELS: dict[str, Any] = {"default": "m", "registry": {"m": {"backend": "mock", "model": "x"}}}


class RecordingTransport:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def __call__(self, target, headers, json, timeout, max_bytes):  # type: ignore[no-untyped-def]
        self.calls.append({"url": target.connect_url, "headers": headers, "json": json})
        return 200, {
            "jsonrpc": "2.0",
            "id": json.get("id"),
            "result": {"content": [], "isError": False},
        }


class DictSecrets:
    def __init__(self, values: dict[str, str]) -> None:
        self._values = values

    def resolve(self, ref: str) -> str | None:
        return self._values.get(ref)


def _service(plugin: PluginConfig, transport: Any, **kw: Any) -> PluginService:
    return PluginService({plugin.name: plugin}, transport=transport, egress=EgressConfig(), **kw)


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://169.254.169.254:8808/mcp",  # metadane chmury (link-local)
        "http://10.0.0.5:8808/mcp",  # prywatny LAN (dane opuszczają host)
        "http://[::ffff:169.254.169.254]:8808/mcp",  # IPv4-mapped bypass
    ],
)
def test_call_ssrf_blocked_per_invocation(endpoint: str) -> None:
    # Egress re-walidowany PRZY wywołaniu — SSRF twardo blokowany, transport nietknięty.
    transport = RecordingTransport()
    plugin = PluginConfig(name="p", endpoint=endpoint, allow_call=True, call_allowlist=["echo"])
    with pytest.raises(EgressError):
        _service(plugin, transport).call("p", "echo", {})
    assert transport.calls == []


def test_call_allow_call_false_never_touches_network() -> None:
    transport = RecordingTransport()
    plugin = PluginConfig(
        name="p", endpoint="http://127.0.0.1:8808", allow_call=False, call_allowlist=[]
    )
    from husarz.plugins.errors import PluginCallDeniedError

    with pytest.raises(PluginCallDeniedError):
        _service(plugin, transport).call("p", "echo", {})
    assert transport.calls == []


def test_allow_call_true_empty_allowlist_rejected_at_config() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        PluginConfig(name="p", endpoint="http://127.0.0.1:8808", allow_call=True, call_allowlist=[])


def test_airgap_rejects_nonloopback_plugin_at_startup() -> None:
    # Bramka startowa airgap: włączona wtyczka MUSI być loopback (M2 — spójne z runtime).
    with pytest.raises(ValueError, match="airgap"):
        HusarzConfig(
            platform={"profile": "airgap"},
            models=_MODELS,
            plugins={"p": PluginConfig(name="p", endpoint="http://10.0.0.5:8808", enabled=True)},
        )


def test_airgap_allows_loopback_plugin() -> None:
    cfg = HusarzConfig(
        platform={"profile": "airgap"},
        models=_MODELS,
        plugins={"p": PluginConfig(name="p", endpoint="http://127.0.0.1:8808", enabled=True)},
    )
    assert "p" in cfg.plugins  # loopback dozwolony


def test_arguments_env_ref_passed_verbatim_not_resolved() -> None:
    # Model wsadza env:-ref w arguments — NIE jest rozwiązywany (sekret NIE eksfiltrowany),
    # a token wtyczki idzie tylko w nagłówku (nie w URL).
    transport = RecordingTransport()
    plugin = PluginConfig(
        name="p",
        endpoint="http://127.0.0.1:8808/mcp",
        allow_call=True,
        call_allowlist=["echo"],
        token_ref="env:TOKEN",
    )
    service = _service(plugin, transport, secrets=DictSecrets({"env:TOKEN": "sekret-tok"}))
    service.call("p", "echo", {"klucz": "env:INNY_SEKRET"})
    sent = transport.calls[0]
    assert sent["json"]["params"]["arguments"] == {"klucz": "env:INNY_SEKRET"}  # VERBATIM
    assert "sekret-tok" not in sent["url"]  # token NIE w URL
    assert sent["headers"].get("Authorization") == "Bearer sekret-tok"  # tylko nagłówek


def test_transport_error_degrades_to_ok_false() -> None:
    class BoomTransport:
        def __call__(self, *a: Any, **k: Any) -> tuple[int, Any]:
            raise PluginTransportError("limit rozmiaru odpowiedzi przekroczony")

    plugin = PluginConfig(
        name="p", endpoint="http://127.0.0.1:8808", allow_call=True, call_allowlist=["echo"]
    )
    tool = PluginTool("plugin_p", "p", _service(plugin, BoomTransport()), max_output_bytes=1000)
    assert tool.call("echo", {}).ok is False  # awaria transportu → ok=False, nie wyjątek
