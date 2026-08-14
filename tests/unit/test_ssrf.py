"""Testy jednostkowe współdzielonej warstwy anty-SSRF i pinowania IP (``husarz.ssrf``).

Wszystko OFFLINE: resolver jest wstrzykiwany, więc żaden test nie odpytuje DNS ani nie
otwiera gniazda. Sprawdzamy klasyfikację hostów, fail-closed przy rozwiązywaniu nazw oraz
poprawność składania ``PinnedTarget`` (connect po IP, Host/SNI po nazwie).
"""

from __future__ import annotations

from typing import Any

import pytest

from husarz.router.egress import EgressError
from husarz.ssrf import (
    HostResolver,
    PinnedTarget,
    build_pinned_target,
    is_blocked_address,
    is_loopback_host,
    is_loopback_name,
    parse_ip_literal,
    pin_fields,
    resolve_and_pin,
    resolve_loopback_name,
)

pytestmark = pytest.mark.unit


def _resolves_to(*addresses: str) -> HostResolver:
    """Resolver testowy zwracający ustalone adresy (pusty = nierozwiązywalna nazwa)."""

    def _resolve(host: str) -> list[str]:
        return list(addresses)

    return _resolve


# --- parse_ip_literal ------------------------------------------------------


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        ("127.0.0.1", "127.0.0.1"),
        ("[::1]", "::1"),
        ("::1", "::1"),
        # IPv4-mapped IPv6 jest ROZWIJANE — inaczej ::ffff:169.254.169.254 udawałby
        # adres „nie-link-local" i przechodziłby przez bramkę metadanych chmury.
        ("::ffff:169.254.169.254", "169.254.169.254"),
        ("[::ffff:10.0.0.1]", "10.0.0.1"),
    ],
)
def test_parse_ip_literal_normalizes(host: str, expected: str) -> None:
    ip = parse_ip_literal(host)
    assert ip is not None
    assert str(ip) == expected


@pytest.mark.parametrize("host", ["example.com", "mcp.vendor.com", "localhost", ""])
def test_parse_ip_literal_returns_none_for_names(host: str) -> None:
    assert parse_ip_literal(host) is None


# --- is_blocked_address ----------------------------------------------------


@pytest.mark.parametrize(
    "address",
    [
        "169.254.169.254",  # metadane chmury (link-local)
        "fe80::1",  # link-local IPv6
        "10.0.0.5",  # RFC 1918
        "192.168.1.10",
        "172.16.0.9",
        "fd00::1",  # ULA
        "0.0.0.0",  # noqa: S104 - to DANE testu (sprawdzamy, że taki adres jest BLOKOWANY)
        "224.0.0.1",  # multicast
        "240.0.0.1",  # zarezerwowany
        # Znaleziska adwersaryjnego przeglądu — stdlib NIE oznacza ich jako prywatne:
        "100.100.100.200",  # CGNAT/RFC 6598 — endpoint metadanych Alibaba Cloud
        "100.64.0.1",
        "fec0::1",  # IPv6 site-local
        "2002:a9fe:a9fe::1",  # 6to4 osadzające 169.254.169.254
        "2001::1",  # Teredo
        "64:ff9b::a9fe:a9fe",  # NAT64 well-known osadzające 169.254.169.254
        "198.18.0.1",  # sieć benchmarkowa
        "192.0.0.1",  # IETF protocol assignments
    ],
)
@pytest.mark.parametrize("allow_loopback", [True, False])
def test_internal_addresses_always_blocked(address: str, allow_loopback: bool) -> None:
    ip = parse_ip_literal(address)
    assert ip is not None
    assert is_blocked_address(ip, allow_loopback=allow_loopback) is True


@pytest.mark.parametrize("address", ["127.0.0.1", "127.5.5.5", "::1"])
def test_loopback_follows_flag(address: str) -> None:
    ip = parse_ip_literal(address)
    assert ip is not None
    assert is_blocked_address(ip, allow_loopback=True) is False
    assert is_blocked_address(ip, allow_loopback=False) is True


@pytest.mark.parametrize("address", ["93.184.216.34", "2606:2800:220:1:248:1893:25c8:1946"])
def test_public_addresses_allowed(address: str) -> None:
    ip = parse_ip_literal(address)
    assert ip is not None
    assert is_blocked_address(ip, allow_loopback=False) is False


# --- is_loopback_host ------------------------------------------------------


@pytest.mark.parametrize("host", ["localhost", "LOCALHOST", "127.0.0.1", "[::1]", "127.9.9.9"])
def test_loopback_hosts_recognized(host: str) -> None:
    assert is_loopback_host(host) is True


@pytest.mark.parametrize("host", ["example.com", "localhost.evil.com", "10.0.0.1"])
def test_non_loopback_hosts(host: str) -> None:
    assert is_loopback_host(host) is False


@pytest.mark.parametrize("host", ["sub.localhost", "MCP.LocalHost", "a.b.localhost"])
def test_localhost_suffix_is_not_trusted_without_dns(host: str) -> None:
    """RFC 6761 tylko ZALECA mapowanie ``*.localhost`` na loopback — glibc potrafi wysłać
    taką nazwę do zwykłego DNS, więc nie może omijać bram na podstawie samego sufiksu."""
    assert is_loopback_host(host) is False
    assert is_loopback_name(host) is True


def test_localhost_name_resolving_outside_loopback_is_rejected() -> None:
    with pytest.raises(EgressError, match="udaje loopback"):
        resolve_loopback_name("mcp.localhost", resolve=_resolves_to("93.184.216.34"))


def test_localhost_name_resolving_to_loopback_is_pinned() -> None:
    target = build_pinned_target(
        "http://mcp.localhost:8808/rpc", allow_loopback=True, resolve=_resolves_to("127.0.0.1")
    )
    assert target.connect_url == "http://127.0.0.1:8808/rpc"
    assert target.host_header == "mcp.localhost:8808"


# --- resolve_and_pin (fail-closed) -----------------------------------------


def test_resolve_and_pin_returns_first_address() -> None:
    pinned = resolve_and_pin(
        "example.com", allow_loopback=False, resolve=_resolves_to("93.184.216.34", "93.184.216.35")
    )
    assert pinned == "93.184.216.34"


def test_unresolvable_name_fails_closed() -> None:
    with pytest.raises(EgressError, match="Nie udało się rozwiązać"):
        resolve_and_pin("nic.example", allow_loopback=False, resolve=_resolves_to())


def test_mixed_records_with_one_internal_are_rejected() -> None:
    """Zatruta odpowiedź DNS NIE staje się wiarygodna przez to, że zawiera też czysty adres."""
    with pytest.raises(EgressError, match="SSRF/DNS-rebinding"):
        resolve_and_pin(
            "mieszany.example",
            allow_loopback=False,
            resolve=_resolves_to("93.184.216.34", "169.254.169.254"),
        )


def test_name_resolving_to_loopback_rejected_when_not_allowed() -> None:
    with pytest.raises(EgressError):
        resolve_and_pin("x.example", allow_loopback=False, resolve=_resolves_to("127.0.0.1"))


def test_garbage_resolver_output_fails_closed() -> None:
    """Resolver zwracający nie-adres (uszkodzony/wrogi) → odmowa, nie przepuszczenie."""
    with pytest.raises(EgressError):
        resolve_and_pin("x.example", allow_loopback=False, resolve=_resolves_to("nie-adres"))


# --- pin_fields ------------------------------------------------------------


def test_pin_fields_keeps_scheme_path_query_and_moves_host_to_header() -> None:
    target = pin_fields("https://example.com/a/b?q=1", "93.184.216.34")
    assert target.connect_url == "https://93.184.216.34/a/b?q=1"
    assert target.host_header == "example.com"
    assert target.sni_hostname == "example.com"
    assert target.pinned_ip == "93.184.216.34"


def test_pin_fields_preserves_explicit_port() -> None:
    target = pin_fields("https://example.com:8443/x", "93.184.216.34")
    assert target.connect_url == "https://93.184.216.34:8443/x"
    assert target.host_header == "example.com:8443"


def test_pin_fields_brackets_ipv6_literal() -> None:
    target = pin_fields("https://example.com/x", "2606:2800:220::1")
    assert target.connect_url == "https://[2606:2800:220::1]/x"


def test_pin_fields_http_has_no_sni() -> None:
    """Dla ``http`` nie ma TLS, więc nie ma czego weryfikować po nazwie."""
    target = pin_fields("http://example.com/x", "93.184.216.34")
    assert target.sni_hostname is None
    assert target.host_header == "example.com"


def test_pin_fields_drops_userinfo() -> None:
    """Poświadczenia z URL NIE są przenoszone do połączenia ani do nagłówka Host."""
    target = pin_fields("https://user:haslo@example.com/x", "93.184.216.34")
    assert "user" not in target.connect_url and "haslo" not in target.connect_url
    assert target.host_header == "example.com"


# --- build_pinned_target (kompozyt) ----------------------------------------


def test_public_literal_connects_directly_without_dns() -> None:
    def _boom(host: str) -> list[str]:  # pragma: no cover - nie powinno być wołane
        raise AssertionError("literał IP nie może wywoływać DNS")

    target = build_pinned_target("https://93.184.216.34/x", allow_loopback=False, resolve=_boom)
    assert target == PinnedTarget.direct("https://93.184.216.34/x")


@pytest.mark.parametrize(
    "url",
    [
        "http://169.254.169.254/latest/meta-data",
        "http://[::ffff:169.254.169.254]/x",
        "http://10.0.0.5/x",
        "https://192.168.1.1/x",
    ],
)
def test_internal_literals_rejected(url: str) -> None:
    with pytest.raises(EgressError, match="SSRF"):
        build_pinned_target(url, allow_loopback=True, resolve=_resolves_to("93.184.216.34"))


def test_loopback_allowed_when_flag_set() -> None:
    target = build_pinned_target(
        "http://localhost:8808/mcp", allow_loopback=True, resolve=_resolves_to()
    )
    assert target.connect_url == "http://localhost:8808/mcp"
    assert target.pinned_ip is None


def test_loopback_rejected_when_flag_unset() -> None:
    with pytest.raises(EgressError, match="loopback"):
        build_pinned_target("http://localhost:8808/x", allow_loopback=False, resolve=_resolves_to())


def test_name_is_resolved_and_pinned() -> None:
    target = build_pinned_target(
        "https://example.com/x", allow_loopback=False, resolve=_resolves_to("93.184.216.34")
    )
    assert target.connect_url == "https://93.184.216.34/x"
    assert target.sni_hostname == "example.com"


def test_name_resolving_to_loopback_rejected_even_with_allow_loopback() -> None:
    """Nazwa publiczna nie może „stać się" loopbackiem — inaczej zatruty DNS kierowałby
    ruch (z tokenem) do usługi na tej maszynie. Loopback intencjonalny idzie literałem."""
    with pytest.raises(EgressError):
        build_pinned_target(
            "https://mcp.vendor.com/x", allow_loopback=True, resolve=_resolves_to("127.0.0.1")
        )


def test_url_without_host_rejected() -> None:
    with pytest.raises(EgressError, match="bez hosta"):
        build_pinned_target("file:///etc/passwd", allow_loopback=False, resolve=_resolves_to())


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com:99999/x",  # poza zakresem 0-65535
        "https://example.com:abc/x",  # nieliczbowy
        "https://example.com:-1/x",
    ],
)
def test_malformed_port_is_egress_error_not_raw_valueerror(url: str) -> None:
    """URL sterowany przez model NIE może wywrócić pętli surowym ``ValueError`` ze stdlib."""
    with pytest.raises(EgressError, match="niepoprawnym portem"):
        build_pinned_target(url, allow_loopback=False, resolve=_resolves_to("93.184.216.34"))


def test_malformed_port_rejected_before_dns() -> None:
    def _boom(host: str) -> list[str]:  # pragma: no cover - nie powinno być wołane
        raise AssertionError("niepoprawny port musi być odrzucony PRZED rozwiązaniem nazwy")

    with pytest.raises(EgressError):
        build_pinned_target("https://example.com:99999/x", allow_loopback=False, resolve=_boom)


# --- Transporty produkcyjne (httpx) ----------------------------------------
# Testy realnych implementacji (HttpxFetcher / HttpxPluginTransport) BEZ sieci: podmieniamy
# ``httpx.Client`` na klienta z ``MockTransport``. Bez tego pin byłby zweryfikowany tylko
# na atrapach, a produkcyjna ścieżka mogłaby po cichu gubić Host/SNI.


@pytest.fixture
def httpx_recorder(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Podstawia ``httpx.Client`` z transportem-atrapą; zwraca podejrzane żądanie."""
    import httpx

    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["host"] = request.headers.get("Host")
        seen["sni"] = request.extensions.get("sni_hostname")
        seen["authorization"] = request.headers.get("Authorization")
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": {"tools": []}})

    real_client = httpx.Client

    def factory(**kwargs: Any) -> httpx.Client:
        return real_client(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(httpx, "Client", factory)
    return seen


def test_httpx_fetcher_connects_to_ip_and_keeps_host_and_sni(
    httpx_recorder: dict[str, Any],
) -> None:
    from husarz.tools.web import HttpxFetcher

    target = pin_fields("https://example.com/a?q=1", "93.184.216.34")
    status, text = HttpxFetcher()(target, timeout=5, max_bytes=1000)
    assert status == 200
    assert httpx_recorder["url"] == "https://93.184.216.34/a?q=1"
    assert httpx_recorder["host"] == "example.com"
    assert httpx_recorder["sni"] == "example.com"


def test_httpx_fetcher_caps_body_at_max_bytes(httpx_recorder: dict[str, Any]) -> None:
    """Twardy sufit działa PODCZAS odczytu — złośliwy serwer nie wyczerpie pamięci."""
    from husarz.tools.web import HttpxFetcher

    target = pin_fields("https://example.com/x", "93.184.216.34")
    _, text = HttpxFetcher()(target, timeout=5, max_bytes=8)
    assert len(text.encode("utf-8")) <= 8


def test_httpx_fetcher_direct_target_sends_no_host_override(httpx_recorder: dict[str, Any]) -> None:
    from husarz.tools.web import HttpxFetcher

    HttpxFetcher()(PinnedTarget.direct("https://93.184.216.34/x"), timeout=5, max_bytes=100)
    assert httpx_recorder["url"] == "https://93.184.216.34/x"
    assert httpx_recorder["host"] == "93.184.216.34"  # domyślny Host z URL, brak nadpisania
    assert httpx_recorder["sni"] is None


def test_httpx_plugin_transport_pins_ip_and_keeps_host_sni_and_bearer(
    httpx_recorder: dict[str, Any],
) -> None:
    from husarz.plugins.client import HttpxPluginTransport

    target = pin_fields("https://mcp.vendor.com/rpc", "93.184.216.34")
    status, data = HttpxPluginTransport()(
        target,
        {"Authorization": "Bearer sekret-tok", "Content-Type": "application/json"},
        {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
        5,
        10_000,
    )
    assert status == 200 and isinstance(data, dict)
    assert httpx_recorder["url"] == "https://93.184.216.34/rpc"
    assert httpx_recorder["host"] == "mcp.vendor.com"
    assert httpx_recorder["sni"] == "mcp.vendor.com"
    assert httpx_recorder["authorization"] == "Bearer sekret-tok"


@pytest.fixture
def httpx_client_kwargs(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Przechwytuje kwargs przekazane do ``httpx.Client`` (kontrola twardych ustawień)."""
    import httpx

    seen: list[dict[str, Any]] = []
    real_client = httpx.Client

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": {"tools": []}})

    def factory(**kwargs: Any) -> httpx.Client:
        seen.append(dict(kwargs))
        return real_client(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(httpx, "Client", factory)
    return seen


def test_production_clients_ignore_environment_proxy_settings(
    httpx_client_kwargs: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """``trust_env=False`` jest OBOWIĄZKOWE: z domyślnym ``True`` zmienne ``HTTPS_PROXY``
    (oraz ``SSLKEYLOGFILE``) przekierowałyby przypięte połączenie przez cudzy serwer —
    czyli obeszłyby całą warstwę pinowania i deny-all egress."""
    from husarz.git.client import HttpxGitTransport
    from husarz.plugins.client import HttpxPluginTransport
    from husarz.tools.web import HttpxFetcher

    monkeypatch.setenv("HTTPS_PROXY", "http://zly-proxy.example:8080")
    target = pin_fields("https://example.com/x", "93.184.216.34")

    HttpxFetcher()(target, timeout=5, max_bytes=1000)
    HttpxPluginTransport()(target, {}, {"jsonrpc": "2.0", "id": 1}, 5, 1000)
    HttpxGitTransport()("GET", target, {}, None, 5)

    assert len(httpx_client_kwargs) == 3
    for kwargs in httpx_client_kwargs:
        assert kwargs["trust_env"] is False
        assert kwargs["verify"] is True
        assert kwargs["follow_redirects"] is False


def test_httpx_git_transport_pins_ip_and_keeps_host_sni_and_bearer(
    httpx_recorder: dict[str, Any],
) -> None:
    """Ścieżka Git niesie token PAT z prawem ZAPISU — pin nie może degradować weryfikacji certu."""
    from husarz.git.client import HttpxGitTransport

    target = pin_fields("https://api.github.com/user/repos", "140.82.121.6")
    status, _ = HttpxGitTransport()("GET", target, {"Authorization": "Bearer sekret-pat"}, None, 5)
    assert status == 200
    assert httpx_recorder["url"] == "https://140.82.121.6/user/repos"
    assert httpx_recorder["host"] == "api.github.com"
    assert httpx_recorder["sni"] == "api.github.com"
    assert httpx_recorder["authorization"] == "Bearer sekret-pat"


def test_all_production_transports_read_in_bounded_chunks(monkeypatch: pytest.MonkeyPatch) -> None:
    """Wszystkie trzy transporty MUSZĄ czytać chunkami: bez `chunk_size` httpx oddaje cały
    zdekompresowany blok naraz (żądania wysyłają `Accept-Encoding: gzip`), więc sprawdzenie
    limitu następuje PO doklejeniu — odpowiedź-bomba przekracza `max_bytes` o rzędy wielkości."""
    import httpx

    from husarz.git.client import HttpxGitTransport
    from husarz.plugins.client import HttpxPluginTransport
    from husarz.tools.web import HttpxFetcher

    seen: list[int | None] = []
    real_iter = httpx.Response.iter_bytes

    def spy(self: httpx.Response, chunk_size: int | None = None):  # type: ignore[no-untyped-def]
        seen.append(chunk_size)
        return real_iter(self, chunk_size=chunk_size)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": {}})

    real_client = httpx.Client
    monkeypatch.setattr(
        httpx, "Client", lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw)
    )
    monkeypatch.setattr(httpx.Response, "iter_bytes", spy)

    target = pin_fields("https://example.com/x", "93.184.216.34")
    HttpxFetcher()(target, timeout=5, max_bytes=1000)
    HttpxPluginTransport()(target, {}, {"jsonrpc": "2.0", "id": 1}, 5, 1000)
    HttpxGitTransport()("GET", target, {}, None, 5)

    # httpx wywołuje `iter_bytes()` także wewnętrznie (bez chunk_size) przy materializacji
    # odpowiedzi — liczą się WYWOŁANIA NASZEGO kodu, czyli te z jawnym rozmiarem.
    explicit = [size for size in seen if size is not None]
    assert len(explicit) == 3  # po jednym z każdego transportu produkcyjnego
    assert all(0 < size <= 64 * 1024 for size in explicit)
