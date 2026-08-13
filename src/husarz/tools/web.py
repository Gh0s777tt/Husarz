"""Narzędzie web — pobieranie treści WYŁĄCZNIE z dozwolonych domen.

Dwie warstwy: allowlista domen narzędzia ORAZ globalna polityka egress
(``check_endpoint_allowed`` z routera — ta sama definicja „lokalny/dozwolony").
Fetcher (HTTP) jest wstrzykiwalny, więc testy nie wykonują połączeń sieciowych.
"""

from __future__ import annotations

import ipaddress
from typing import Protocol, runtime_checkable

from husarz.config.net import endpoint_host
from husarz.config.schema import EgressConfig
from husarz.router.egress import EgressError, check_endpoint_allowed
from husarz.tools.base import ToolResult

DEFAULT_MAX_BYTES = 2_000_000
DEFAULT_TIMEOUT = 20


def _is_internal_ip_literal(host: str) -> bool:
    """True, gdy host to literalny adres IP wewnętrzny/zarezerwowany (ochrona SSRF).

    Blokuje m.in. loopback, prywatne (RFC 1918/ULA), link-local (169.254/16 —
    endpoint metadanych chmury) oraz adresy zarezerwowane. Nie chroni przed
    DNS rebindingiem (domena z allowlisty wskazująca na adres wewnętrzny) —
    to wymaga pinowania IP i jest odłożone (patrz docs/NARZEDZIA.md).
    """
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return (
        ip.is_loopback
        or ip.is_private
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


@runtime_checkable
class Fetcher(Protocol):
    """Warstwa HTTP: zwraca (status, treść). Testy wstrzykują własną implementację."""

    def __call__(self, url: str, *, timeout: int, max_bytes: int) -> tuple[int, str]: ...


class HttpxFetcher:
    """Produkcyjny fetcher oparty o httpx (import leniwy)."""

    def __call__(self, url: str, *, timeout: int, max_bytes: int) -> tuple[int, str]:
        import httpx  # noqa: PLC0415 - import leniwy

        response = httpx.get(url, timeout=timeout, follow_redirects=False)
        return response.status_code, response.text[:max_bytes]


class WebTool:
    """Pobieranie stron z domen dozwolonych (allowlista narzędzia + egress)."""

    name = "web"

    def __init__(
        self,
        fetcher: Fetcher,
        *,
        domain_allowlist: list[str],
        egress: EgressConfig,
        max_bytes: int = DEFAULT_MAX_BYTES,
        timeout: int = DEFAULT_TIMEOUT,
    ) -> None:
        self._fetcher = fetcher
        self._domains = list(domain_allowlist)
        self._egress = egress
        self._max_bytes = max_bytes
        self._timeout = timeout

    def _domain_allowed(self, host: str) -> bool:
        return any(host == domain or host.endswith(f".{domain}") for domain in self._domains)

    def fetch(self, url: str) -> ToolResult:
        host = endpoint_host(url)
        if host is None:
            return ToolResult(self.name, ok=False, error=f"Nie rozpoznano hosta w URL: {url!r}.")
        # Ochrona SSRF: narzędzie web nie łączy się z literalnymi adresami wewnętrznymi
        # (loopback/prywatne/link-local/metadane) niezależnie od allowlisty i egress.
        if _is_internal_ip_literal(host):
            return ToolResult(
                self.name,
                ok=False,
                error=f"Host '{host}' to adres wewnętrzny/zarezerwowany (SSRF).",
            )
        if not self._domain_allowed(host):
            return ToolResult(
                self.name, ok=False, error=f"Domena '{host}' spoza allowlisty narzędzia web."
            )
        # Globalna polityka egress (deny-all) — druga warstwa.
        try:
            check_endpoint_allowed(url, self._egress)
        except EgressError as exc:
            return ToolResult(self.name, ok=False, error=str(exc))

        status, text = self._fetcher(url, timeout=self._timeout, max_bytes=self._max_bytes)
        return ToolResult(
            self.name,
            ok=200 <= status < 300,
            output=text,
            error="" if 200 <= status < 300 else f"HTTP {status}",
            metadata={"status": status, "host": host},
        )
