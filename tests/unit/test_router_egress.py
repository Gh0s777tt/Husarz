"""Testy bramki egress routera (deny-all na ścieżce wywołania modelu)."""

from __future__ import annotations

import pytest

from husarz.config.schema import EgressConfig
from husarz.router import ChatMessage, ChatRequest, EgressError, ModelRouter
from husarz.router.egress import check_endpoint_allowed

pytestmark = pytest.mark.unit


def _egress(policy: str = "deny", allowlist: list[str] | None = None) -> EgressConfig:
    return EgressConfig(default_policy=policy, allowlist=allowlist or [])


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://localhost:8000/v1",
        "http://127.0.0.1:8000",
        "http://192.168.1.5:11434/v1",
        "http://model.local/v1",
        None,
    ],
)
def test_local_endpoints_always_allowed(endpoint) -> None:
    # Nie rzuca — lokalne/prywatne nie są ruchem do WAN.
    check_endpoint_allowed(endpoint, _egress("deny"))


def test_remote_denied_by_default() -> None:
    with pytest.raises(EgressError, match="deny-all"):
        check_endpoint_allowed("https://api.openai.com/v1", _egress("deny"))


def test_remote_allowed_when_in_allowlist() -> None:
    check_endpoint_allowed("https://api.openai.com/v1", _egress("deny", ["api.openai.com"]))


def test_remote_allowed_as_subdomain() -> None:
    check_endpoint_allowed("https://eu.api.example.com/v1", _egress("deny", ["example.com"]))


def test_allow_policy_permits_remote() -> None:
    check_endpoint_allowed("https://api.openai.com/v1", _egress("allow"))


def test_router_skips_egress_denied_and_falls_back_to_local(make_config) -> None:
    """Router pomija zdalny model zablokowany przez egress i używa lokalnego fallbacku."""
    registry = {
        "remote": {
            "backend": "openai_compat",
            "model": "r",
            "endpoint": "https://api.openai.com/v1",
            "fallback": ["local"],
        },
        "local": {"backend": "mock", "model": "l"},
    }
    config = make_config(registry=registry, default="remote")  # egress deny (domyślnie)
    router = ModelRouter(config)  # domyślna fabryka: 'remote' nawet nie zbudowany (egress blok)
    resp = router.complete(ChatRequest(messages=[ChatMessage("user", "x")]))
    assert resp.model == "local"
