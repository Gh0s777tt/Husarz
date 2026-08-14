"""Niezmienniki bezpieczeństwa konektora MCP (Etap 12b).

Skupienie: anty-SSRF endpointu (odwrócona polaryzacja — loopback OK, adresy
wewnętrzne/metadanych TWARDY BLOK, publiczne wymagają https + allowlisty egress),
brak wyjścia na sieć przy odmowie, oraz token WYŁĄCZNIE jako referencja.
"""

from __future__ import annotations

from typing import Any

import pytest

from husarz.config.schema import EgressConfig, EgressPolicy, PluginConfig
from husarz.plugins import PluginService
from husarz.plugins.client import _endpoint_target
from husarz.plugins.errors import PluginError
from husarz.router.egress import EgressError
from husarz.ssrf import PinnedTarget

pytestmark = pytest.mark.security

_DENY = EgressConfig()  # default_policy = deny


class RecordingTransport:
    """Transport, który zapisuje, czy w ogóle został wywołany (kontrola „brak sieci")."""

    def __init__(self) -> None:
        self.called = False

    def __call__(
        self,
        target: PinnedTarget,
        headers: dict[str, str],
        json: dict[str, Any],
        timeout: int,
        max_bytes: int,
    ) -> tuple[int, Any]:
        self.called = True
        return 200, {"result": {"tools": []}}


def _resolves_to(*ips: str):  # noqa: ANN202 - fabryka fałszywego resolvera (bez DNS)
    return lambda host: list(ips)


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://127.0.0.1:8808/mcp",
        "http://localhost:9000",
        "http://[::1]:8808",
    ],
)
def test_loopback_allowed(endpoint: str) -> None:
    _endpoint_target(endpoint, _DENY)  # nie rzuca — literał/`localhost`, bez DNS


def test_localhost_suffix_allowed_only_after_dns_proof() -> None:
    """`*.localhost` przechodzi WYŁĄCZNIE, gdy DNS potwierdzi loopback (ADR-0020)."""
    target = _endpoint_target("http://sub.localhost:8808", _DENY, resolve=_resolves_to("127.0.0.1"))
    assert target.connect_url == "http://127.0.0.1:8808"
    with pytest.raises(EgressError, match="udaje loopback"):
        _endpoint_target("http://sub.localhost:8808", _DENY, resolve=_resolves_to("93.184.216.34"))


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://169.254.169.254/latest/meta-data",  # metadane chmury (link-local)
        "http://[::ffff:169.254.169.254]/x",  # IPv4-mapped IPv6 — domknięty bypass
        "http://10.0.0.5:8808",  # prywatny RFC1918
        "http://192.168.1.10",  # prywatny
        "http://172.16.0.9",  # prywatny
        "http://0.0.0.0:8808",  # unspecified
        "http://224.0.0.1",  # multicast
    ],
)
def test_internal_and_metadata_hard_blocked(endpoint: str) -> None:
    with pytest.raises(EgressError):
        _endpoint_target(endpoint, _DENY)


def test_public_http_rejected_requires_https() -> None:
    with pytest.raises(PluginError):
        _endpoint_target("http://mcp.example.com", _DENY)


def test_public_https_without_allowlist_denied() -> None:
    with pytest.raises(EgressError):
        _endpoint_target("https://mcp.example.com", _DENY, resolve=_resolves_to("93.184.216.34"))


def test_public_https_allowlisted_ok() -> None:
    egress = EgressConfig(allowlist=["example.com"])
    # Rozwiązuje się na publiczny adres → dozwolone (bez realnego DNS).
    _endpoint_target("https://mcp.example.com", egress, resolve=_resolves_to("93.184.216.34"))


def test_userinfo_rejected() -> None:
    with pytest.raises(PluginError):
        _endpoint_target("http://user:pass@127.0.0.1:8808", _DENY)


def test_allow_policy_permits_public_https() -> None:
    egress = EgressConfig(default_policy=EgressPolicy.ALLOW)
    _endpoint_target("https://mcp.example.com", egress, resolve=_resolves_to("93.184.216.34"))


# --- Anty-DNS-rebinding: nazwa rozwiązująca się na adres wewnętrzny/metadanych ---


def test_domain_resolving_to_metadata_blocked() -> None:
    # Nazwa na allowliście, ale rekord A wskazuje metadane chmury → TWARDY BLOK.
    egress = EgressConfig(allowlist=["vendor.com"])
    with pytest.raises(EgressError):
        _endpoint_target("https://mcp.vendor.com", egress, resolve=_resolves_to("169.254.169.254"))


def test_domain_resolving_to_private_blocked_under_allow_policy() -> None:
    egress = EgressConfig(default_policy=EgressPolicy.ALLOW)
    with pytest.raises(EgressError):
        _endpoint_target("https://x.attacker.net", egress, resolve=_resolves_to("10.0.0.9"))


def test_unresolvable_domain_fails_closed() -> None:
    egress = EgressConfig(allowlist=["vendor.com"])
    with pytest.raises(EgressError):
        _endpoint_target("https://mcp.vendor.com", egress, resolve=_resolves_to())


def test_empty_allowlist_entry_rejected_at_config() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        EgressConfig(allowlist=[""])
    with pytest.raises(ValidationError):
        EgressConfig(allowlist=["https://evil.com"])  # schemat/port/ścieżka niedozwolone


def test_blocked_endpoint_never_hits_transport() -> None:
    # Wtyczka wskazująca metadane chmury — odmowa egress PRZED jakimkolwiek wyjściem na sieć.
    transport = RecordingTransport()
    service = PluginService(
        {"evil": PluginConfig(name="evil", endpoint="http://169.254.169.254/mcp")},
        egress=_DENY,
        transport=transport,
    )
    with pytest.raises(EgressError):
        service.discover("evil")
    assert transport.called is False  # SSRF zablokowane bez połączenia


def test_config_rejects_raw_token_reference() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        PluginConfig(name="p", endpoint="http://127.0.0.1:8808", token_ref="surowy-sekret")
