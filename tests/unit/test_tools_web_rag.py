"""Testy narzędzi web (allowlista domen + egress) i rag (in-memory)."""

from __future__ import annotations

import pytest

from husarz.config.schema import EgressConfig
from husarz.ssrf import PinnedTarget
from husarz.tools import InMemoryRagBackend, RagTool, WebTool

pytestmark = pytest.mark.unit


# Resolver testowy — publiczny adres dla KAŻDEJ nazwy (żaden test nie odpytuje DNS).
def _fake_resolve(host: str) -> list[str]:
    return ["93.184.216.34"]


class FakeFetcher:
    def __init__(self, status: int = 200, text: str = "witaj") -> None:
        self.calls: list[str] = []
        self.targets: list[PinnedTarget] = []
        self._status = status
        self._text = text

    def __call__(self, target: PinnedTarget, *, timeout: int, max_bytes: int) -> tuple[int, str]:
        self.calls.append(target.connect_url)
        self.targets.append(target)
        return self._status, self._text


def _egress(policy: str = "deny", allow: list[str] | None = None) -> EgressConfig:
    return EgressConfig(default_policy=policy, allowlist=allow or [])


# --- WebTool ---------------------------------------------------------------


def test_web_fetches_allowed_domain() -> None:
    fetcher = FakeFetcher(text="strona")
    tool = WebTool(
        fetcher,
        domain_allowlist=["example.com"],
        egress=_egress("deny", ["example.com"]),
        resolve=_fake_resolve,
    )
    result = tool.fetch("https://example.com/page")
    assert result.ok
    assert result.output == "strona"
    # Połączenie idzie do PRZYPIĘTEGO adresu, a Host/SNI zostają przy nazwie.
    assert fetcher.calls == ["https://93.184.216.34/page"]
    target = fetcher.targets[0]
    assert target.host_header == "example.com"
    assert target.sni_hostname == "example.com"
    assert result.metadata["pinned_ip"] == "93.184.216.34"


def test_web_blocks_domain_outside_tool_allowlist() -> None:
    fetcher = FakeFetcher()
    tool = WebTool(
        fetcher,
        domain_allowlist=["example.com"],
        egress=_egress("allow"),
        resolve=_fake_resolve,
    )
    result = tool.fetch("https://evil.example.org/x")
    assert result.ok is False
    assert "allowlisty" in result.error
    assert fetcher.calls == []


def test_web_blocks_when_egress_denies() -> None:
    fetcher = FakeFetcher()
    # Domena na allowliście narzędzia, ale globalny egress = deny z pustą allowlistą.
    tool = WebTool(
        fetcher, domain_allowlist=["example.com"], egress=_egress("deny", []), resolve=_fake_resolve
    )
    result = tool.fetch("https://example.com/x")
    assert result.ok is False
    assert fetcher.calls == []


def test_web_allows_subdomain() -> None:
    fetcher = FakeFetcher()
    tool = WebTool(
        fetcher, domain_allowlist=["example.com"], egress=_egress("allow"), resolve=_fake_resolve
    )
    assert tool.fetch("https://docs.example.com/x").ok


def test_web_reports_http_error_status() -> None:
    tool = WebTool(
        FakeFetcher(status=404, text="nope"),
        domain_allowlist=["example.com"],
        egress=_egress("allow"),
        resolve=_fake_resolve,
    )
    result = tool.fetch("https://example.com/x")
    assert result.ok is False
    assert "404" in result.error


# --- RagTool ---------------------------------------------------------------


def test_rag_add_and_search() -> None:
    tool = RagTool(InMemoryRagBackend())
    tool.add("Bielik to polski model językowy")
    tool.add("Docker uruchamia kontenery")
    result = tool.search("polski model")
    assert result.ok
    assert "Bielik" in result.output
    assert result.metadata["count"] == 1


def test_rag_search_no_match() -> None:
    tool = RagTool(InMemoryRagBackend())
    tool.add("coś tam")
    result = tool.search("zupełnie inne frazy")
    assert result.ok
    assert result.metadata["count"] == 0
