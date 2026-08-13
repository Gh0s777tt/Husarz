"""Testy narzędzi web (allowlista domen + egress) i rag (in-memory)."""

from __future__ import annotations

import pytest

from husarz.config.schema import EgressConfig
from husarz.tools import InMemoryRagBackend, RagTool, WebTool

pytestmark = pytest.mark.unit


class FakeFetcher:
    def __init__(self, status: int = 200, text: str = "witaj") -> None:
        self.calls: list[str] = []
        self._status = status
        self._text = text

    def __call__(self, url: str, *, timeout: int, max_bytes: int) -> tuple[int, str]:
        self.calls.append(url)
        return self._status, self._text


def _egress(policy: str = "deny", allow: list[str] | None = None) -> EgressConfig:
    return EgressConfig(default_policy=policy, allowlist=allow or [])


# --- WebTool ---------------------------------------------------------------


def test_web_fetches_allowed_domain() -> None:
    fetcher = FakeFetcher(text="strona")
    tool = WebTool(
        fetcher, domain_allowlist=["example.com"], egress=_egress("deny", ["example.com"])
    )
    result = tool.fetch("https://example.com/page")
    assert result.ok
    assert result.output == "strona"
    assert fetcher.calls == ["https://example.com/page"]


def test_web_blocks_domain_outside_tool_allowlist() -> None:
    fetcher = FakeFetcher()
    tool = WebTool(fetcher, domain_allowlist=["example.com"], egress=_egress("allow"))
    result = tool.fetch("https://evil.example.org/x")
    assert result.ok is False
    assert "allowlisty" in result.error
    assert fetcher.calls == []


def test_web_blocks_when_egress_denies() -> None:
    fetcher = FakeFetcher()
    # Domena na allowliście narzędzia, ale globalny egress = deny z pustą allowlistą.
    tool = WebTool(fetcher, domain_allowlist=["example.com"], egress=_egress("deny", []))
    result = tool.fetch("https://example.com/x")
    assert result.ok is False
    assert fetcher.calls == []


def test_web_allows_subdomain() -> None:
    fetcher = FakeFetcher()
    tool = WebTool(fetcher, domain_allowlist=["example.com"], egress=_egress("allow"))
    assert tool.fetch("https://docs.example.com/x").ok


def test_web_reports_http_error_status() -> None:
    tool = WebTool(
        FakeFetcher(status=404, text="nope"),
        domain_allowlist=["example.com"],
        egress=_egress("allow"),
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
