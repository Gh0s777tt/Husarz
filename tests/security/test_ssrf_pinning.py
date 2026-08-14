"""Niezmienniki bezpieczeństwa pinowania IP na ścieżkach wychodzących (Etap 15, ADR-0020).

Zakres: narzędzie ``web`` oraz konektor MCP (``husarz.plugins``). Wszystko OFFLINE —
resolver DNS i transport/fetcher są wstrzykiwane, więc żaden test nie otwiera gniazda.

Sprawdzane niezmienniki:

1. nazwa rozwiązująca się na adres wewnętrzny/metadanych jest blokowana MIMO allowlisty,
2. odmowa następuje PRZED jakimkolwiek wyjściem na sieć (fetcher/transport nietknięty),
3. połączenie idzie do PRZYPIĘTEGO literału IP, a ``Host``/SNI zostają przy nazwie
   (czyli TLS nie jest degradowany, a drugie rozwiązanie DNS nie istnieje),
4. pin jest ŚWIEŻY dla każdej operacji (nie starzeje się między wywołaniami),
5. loopback: zabroniony dla ``web`` (także jako nazwa), dozwolony dla konektora MCP.
"""

from __future__ import annotations

from typing import Any

import pytest

from husarz.config.schema import EgressConfig, EgressPolicy, PluginConfig
from husarz.git import GitService
from husarz.git.models import GitConnection, GitProviderKind
from husarz.plugins import PluginService
from husarz.router.egress import EgressError
from husarz.ssrf import PinnedTarget
from husarz.tools import FetchError, WebTool

pytestmark = pytest.mark.security

_METADATA = "169.254.169.254"
_PUBLIC = "93.184.216.34"


class CountingResolver:
    """Resolver testowy: liczy wywołania (kontrola świeżości pinu) i zwraca ustalone adresy."""

    def __init__(self, *addresses: str) -> None:
        self.addresses = list(addresses)
        self.calls: list[str] = []

    def __call__(self, host: str) -> list[str]:
        self.calls.append(host)
        return list(self.addresses)


class RecordingFetcher:
    """Fetcher testowy dla ``web`` — zapisuje cele; wywołanie = „poszło na sieć"."""

    def __init__(self) -> None:
        self.targets: list[PinnedTarget] = []

    def __call__(self, target: PinnedTarget, *, timeout: int, max_bytes: int) -> tuple[int, str]:
        self.targets.append(target)
        return 200, "tresc"


class RecordingTransport:
    """Transport testowy dla MCP — zapisuje cele i nagłówki; wywołanie = „poszło na sieć"."""

    def __init__(self) -> None:
        self.targets: list[PinnedTarget] = []
        self.headers: list[dict[str, str]] = []

    def __call__(
        self,
        target: PinnedTarget,
        headers: dict[str, str],
        json: dict[str, Any],
        timeout: int,
        max_bytes: int,
    ) -> tuple[int, Any]:
        self.targets.append(target)
        self.headers.append(dict(headers))
        return 200, {"jsonrpc": "2.0", "id": json.get("id"), "result": {"tools": []}}


class DictSecrets:
    def __init__(self, values: dict[str, str]) -> None:
        self._values = values

    def resolve(self, ref: str) -> str | None:
        return self._values.get(ref)


def _web(fetcher: RecordingFetcher, resolver: CountingResolver, *, allow: bool = False) -> WebTool:
    egress = (
        EgressConfig(default_policy=EgressPolicy.ALLOW)
        if allow
        else EgressConfig(allowlist=["example.com"])
    )
    return WebTool(
        fetcher,
        domain_allowlist=["example.com", "localhost"],
        egress=egress,
        resolve=resolver,
    )


# --- Narzędzie web ---------------------------------------------------------


def test_web_allowlisted_domain_resolving_to_metadata_is_blocked() -> None:
    """Allowlista domen NIE jest zgodą na adres metadanych — rebinding zablokowany."""
    fetcher, resolver = RecordingFetcher(), CountingResolver(_METADATA)
    result = _web(fetcher, resolver).fetch("https://example.com/x")
    assert result.ok is False
    assert "SSRF" in result.error
    assert fetcher.targets == []  # odmowa PRZED wyjściem na sieć


def test_web_mixed_records_with_one_internal_are_blocked() -> None:
    fetcher, resolver = RecordingFetcher(), CountingResolver(_PUBLIC, "10.0.0.7")
    result = _web(fetcher, resolver).fetch("https://example.com/x")
    assert result.ok is False and fetcher.targets == []


def test_web_unresolvable_domain_fails_closed() -> None:
    fetcher, resolver = RecordingFetcher(), CountingResolver()
    result = _web(fetcher, resolver).fetch("https://example.com/x")
    assert result.ok is False
    assert "rozwiązać" in result.error
    assert fetcher.targets == []


def test_web_connects_to_pinned_ip_with_original_host_and_sni() -> None:
    fetcher, resolver = RecordingFetcher(), CountingResolver(_PUBLIC)
    result = _web(fetcher, resolver).fetch("https://example.com/a?q=1")
    assert result.ok
    target = fetcher.targets[0]
    assert target.connect_url == f"https://{_PUBLIC}/a?q=1"  # połączenie po IP
    assert target.host_header == "example.com"  # nagłówek Host po nazwie
    assert target.sni_hostname == "example.com"  # certyfikat weryfikowany po nazwie
    assert resolver.calls == ["example.com"]  # DOKŁADNIE jedno rozwiązanie


def test_web_pin_is_fresh_for_every_fetch() -> None:
    """Pin nie jest cache'owany między wywołaniami — każde pobranie waliduje od nowa."""
    fetcher, resolver = RecordingFetcher(), CountingResolver(_PUBLIC)
    tool = _web(fetcher, resolver)
    tool.fetch("https://example.com/1")
    tool.fetch("https://example.com/2")
    assert resolver.calls == ["example.com", "example.com"]


def test_web_transport_failure_degrades_to_result_not_exception() -> None:
    """Awaria sieci (``FetchError``) nie może wywrócić pętli agenta — parytet z wtyczkami."""

    def _boom(target: PinnedTarget, *, timeout: int, max_bytes: int) -> tuple[int, str]:
        raise FetchError("Błąd transportu HTTP narzędzia web.")

    tool = WebTool(
        _boom,
        domain_allowlist=["example.com"],
        egress=EgressConfig(default_policy=EgressPolicy.ALLOW),
        resolve=CountingResolver(_PUBLIC),
    )
    result = tool.fetch("https://example.com/x")
    assert result.ok is False
    assert "transportu" in result.error
    # Komunikat generyczny — bez URL ani wnętrzności httpx.
    assert "example.com" not in result.error


def test_web_malformed_port_degrades_to_result_not_exception() -> None:
    """Kontrakt „narzędzie NIGDY nie rzuca": URL od modelu z chorym portem → ``ok=False``,
    nie surowy ``ValueError`` wywracający pętlę agenta."""
    fetcher, resolver = RecordingFetcher(), CountingResolver(_PUBLIC)
    result = _web(fetcher, resolver, allow=True).fetch("https://example.com:99999/x")
    assert result.ok is False
    assert "port" in result.error.lower()
    assert fetcher.targets == [] and resolver.calls == []


def test_web_rejects_loopback_by_name_even_when_allowlisted() -> None:
    """Regresja: ``localhost`` na allowliście narzędzia + egress ``allow`` NIE otwiera
    dostępu do usług na maszynie operatora (dawniej literał był blokowany, nazwa nie)."""
    fetcher, resolver = RecordingFetcher(), CountingResolver(_PUBLIC)
    result = _web(fetcher, resolver, allow=True).fetch("http://localhost:8000/admin")
    assert result.ok is False
    assert "loopback" in result.error
    assert fetcher.targets == []
    assert resolver.calls == []  # loopback rozpoznany bez DNS


# --- Konektor MCP (wtyczki) ------------------------------------------------


def _service(
    transport: RecordingTransport, resolver: CountingResolver, endpoint: str
) -> PluginService:
    return PluginService(
        {"p": PluginConfig(name="p", endpoint=endpoint, token_ref="env:TOK")},
        secrets=DictSecrets({"env:TOK": "sekret-tok"}),
        egress=EgressConfig(allowlist=["vendor.com"]),
        transport=transport,
        resolve=resolver,
    )


def test_plugin_domain_resolving_to_metadata_never_reaches_transport() -> None:
    transport, resolver = RecordingTransport(), CountingResolver(_METADATA)
    service = _service(transport, resolver, "https://mcp.vendor.com/rpc")
    with pytest.raises(EgressError):
        service.discover("p")
    assert transport.targets == []


def test_plugin_connects_to_pinned_ip_and_keeps_token_out_of_url() -> None:
    transport, resolver = RecordingTransport(), CountingResolver(_PUBLIC)
    _service(transport, resolver, "https://mcp.vendor.com/rpc").discover("p")
    target = transport.targets[0]
    assert target.connect_url == f"https://{_PUBLIC}/rpc"
    assert target.host_header == "mcp.vendor.com"
    assert target.sni_hostname == "mcp.vendor.com"
    assert "sekret-tok" not in target.connect_url
    assert transport.headers[0]["Authorization"] == "Bearer sekret-tok"


def test_plugin_pin_is_fresh_for_every_operation() -> None:
    """Konektor budowany jest per operację → pin nie starzeje się między wywołaniami."""
    transport, resolver = RecordingTransport(), CountingResolver(_PUBLIC)
    service = _service(transport, resolver, "https://mcp.vendor.com/rpc")
    service.discover("p")
    service.discover("p")
    assert resolver.calls == ["mcp.vendor.com", "mcp.vendor.com"]


def test_plugin_loopback_endpoint_needs_no_dns() -> None:
    """Lokalny serwer MCP (główny przypadek) łączy się wprost — bez DNS i bez pinu."""
    transport, resolver = RecordingTransport(), CountingResolver()
    _service(transport, resolver, "http://127.0.0.1:8808/mcp").discover("p")
    target = transport.targets[0]
    assert target.connect_url == "http://127.0.0.1:8808/mcp"
    assert target.pinned_ip is None and target.host_header is None
    assert resolver.calls == []


def test_plugin_name_resolving_to_loopback_is_blocked() -> None:
    """Publiczna nazwa nie może wskazywać loopbacku — inaczej zatruty DNS wysłałby token
    Bearer do usługi działającej na tej samej maszynie."""
    transport, resolver = RecordingTransport(), CountingResolver("127.0.0.1")
    service = _service(transport, resolver, "https://mcp.vendor.com/rpc")
    with pytest.raises(EgressError):
        service.discover("p")
    assert transport.targets == []


def test_plugin_denied_egress_does_not_even_resolve_dns() -> None:
    """Odmowa allowlisty egress następuje PRZED rozwiązaniem nazwy (brak wycieku do DNS)."""
    transport, resolver = RecordingTransport(), CountingResolver(_PUBLIC)
    service = _service(transport, resolver, "https://mcp.obcy.net/rpc")
    with pytest.raises(EgressError):
        service.discover("p")
    assert resolver.calls == [] and transport.targets == []


# --- Utwardzenia z adwersaryjnego przeglądu (Etap 15) ----------------------


@pytest.mark.parametrize(
    "address",
    [
        "100.100.100.200",  # CGNAT — endpoint metadanych Alibaba Cloud
        "fec0::1",  # IPv6 site-local
        "2002:a9fe:a9fe::1",  # 6to4 osadzające 169.254.169.254
        "64:ff9b::a9fe:a9fe",  # NAT64 osadzające 169.254.169.254
    ],
)
def test_web_blocks_addresses_stdlib_calls_public(address: str) -> None:
    """`ipaddress` NIE oznacza tych adresów jako prywatne — bramka musi je znać sama."""
    fetcher, resolver = RecordingFetcher(), CountingResolver(address)
    result = _web(fetcher, resolver).fetch("https://example.com/latest/meta-data/")
    assert result.ok is False and fetcher.targets == []


@pytest.mark.parametrize(
    "address", ["100.100.100.200", "fec0::1", "2002:a9fe:a9fe::1", "64:ff9b::a9fe:a9fe"]
)
def test_plugin_blocks_addresses_stdlib_calls_public(address: str) -> None:
    transport, resolver = RecordingTransport(), CountingResolver(address)
    service = _service(transport, resolver, "https://mcp.vendor.com/rpc")
    with pytest.raises(EgressError):
        service.discover("p")
    assert transport.targets == []  # token Bearer nigdzie nie poleciał


def test_plugin_localhost_suffix_must_prove_loopback_by_dns() -> None:
    """`mcp.localhost` NIE jest przepustką: bez tego omijałby wymóg https i allowlistę
    egress, a `getaddrinfo` potrafi wysłać taką nazwę do zwykłego DNS."""
    transport, resolver = RecordingTransport(), CountingResolver("93.184.216.34")
    service = _service(transport, resolver, "http://mcp.localhost:8808/rpc")
    with pytest.raises(EgressError, match="udaje loopback"):
        service.discover("p")
    assert transport.targets == []


def test_plugin_localhost_suffix_resolving_to_loopback_is_pinned() -> None:
    transport, resolver = RecordingTransport(), CountingResolver("127.0.0.1")
    _service(transport, resolver, "http://mcp.localhost:8808/rpc").discover("p")
    assert transport.targets[0].connect_url == "http://127.0.0.1:8808/rpc"


def test_web_rejects_non_http_schemes() -> None:
    fetcher, resolver = RecordingFetcher(), CountingResolver(_PUBLIC)
    for url in ("ftp://example.com/x", "gopher://example.com/x", "ws://example.com/x"):
        result = _web(fetcher, resolver, allow=True).fetch(url)
        assert result.ok is False and "http(s)" in result.error
    assert fetcher.targets == []


def test_web_internal_suffix_does_not_bypass_egress_allowlist() -> None:
    """`.internal`/`.local` są „lokalne" dla routera modeli, ale NIE mogą być furtką dla
    narzędzia web — inaczej profil airgap traciłby niezmiennik „brak WAN"."""
    fetcher, resolver = RecordingFetcher(), CountingResolver(_PUBLIC)
    tool = WebTool(
        fetcher,
        domain_allowlist=["corp.internal", "printer.local"],
        egress=EgressConfig(),  # deny + pusta allowlista
        resolve=resolver,
    )
    for url in ("https://corp.internal/x", "https://printer.local/x"):
        result = tool.fetch(url)
        assert result.ok is False
        assert "Egress zabroniony" in result.error
    assert fetcher.targets == [] and resolver.calls == []


def test_denial_message_does_not_leak_resolved_internal_address() -> None:
    """Komunikat odmowy wraca do MODELU — nie może być kanałem rozpoznania sieci."""
    fetcher, resolver = RecordingFetcher(), CountingResolver("10.11.12.13")
    result = _web(fetcher, resolver).fetch("https://example.com/x")
    assert result.ok is False
    assert "10.11.12.13" not in result.error


def test_resolver_unicode_error_fails_closed_not_crash(monkeypatch: pytest.MonkeyPatch) -> None:
    """`getaddrinfo` koduje nazwę kodekiem `idna` i dla etykiety >63 znaków rzuca
    `UnicodeEncodeError` — to NIE `OSError`, więc bez jawnej obsługi wyjątek uciekłby
    poza bramkę i wywrócił pętlę zamiast dać odmowę (fail-open na wyjątek)."""
    import socket as _socket

    from husarz.ssrf import default_resolve

    def _idna_boom(*args: Any, **kwargs: Any) -> Any:
        raise UnicodeEncodeError("idna", "x", 0, 1, "label empty or too long")

    monkeypatch.setattr(_socket, "getaddrinfo", _idna_boom)
    assert default_resolve("a" * 64 + ".example.invalid") == []


# --- Integracje Git (Etap 15b) ---------------------------------------------
# Ścieżka Git niesie token z prawem ZAPISU do repozytoriów, więc jej polaryzacja jest
# trzecia: loopback ZABRONIONY (jak `web`), ale prywatna sieć operatora DOZWOLONA
# (samodzielnie hostowany GitLab to legalny scenariusz suwerenności).


class RecordingGitTransport:
    """Transport testowy dla Git — zapisuje cele i nagłówki; wywołanie = „poszło na sieć"."""

    def __init__(self) -> None:
        self.targets: list[PinnedTarget] = []
        self.headers: list[dict[str, str]] = []

    def __call__(
        self,
        method: str,
        target: PinnedTarget,
        headers: dict[str, str],
        json: dict[str, Any] | None,
        timeout: int,
    ) -> tuple[int, Any]:
        self.targets.append(target)
        self.headers.append(dict(headers))
        return 200, []


def _git_service(
    transport: RecordingGitTransport, resolver: CountingResolver, api_base: str
) -> GitService:
    service = GitService(
        secrets=DictSecrets({"env:GH": "sekret-pat"}),
        egress=EgressConfig(allowlist=["github.com", "git.firma.pl"]),
        transport=transport,
        resolve=resolver,
    )
    service.add(
        GitConnection(
            name="gh", provider=GitProviderKind.GITHUB, api_base=api_base, token_ref="env:GH"
        )
    )
    return service


def test_git_domain_resolving_to_metadata_never_reaches_transport() -> None:
    """Domena z allowlisty rozwiązana na metadane chmury — token PAT nigdzie nie leci."""
    transport, resolver = RecordingGitTransport(), CountingResolver(_METADATA)
    service = _git_service(transport, resolver, "https://api.github.com")
    with pytest.raises(EgressError):
        service.provider_for("gh").list_repositories()
    assert transport.targets == []


def test_git_connects_to_pinned_ip_and_keeps_token_in_header_only() -> None:
    transport, resolver = RecordingGitTransport(), CountingResolver(_PUBLIC)
    _git_service(transport, resolver, "https://api.github.com").provider_for(
        "gh"
    ).list_repositories()
    target = transport.targets[0]
    assert target.connect_url == f"https://{_PUBLIC}/user/repos?per_page=100&sort=updated"
    assert target.host_header == "api.github.com"  # Host po nazwie
    assert target.sni_hostname == "api.github.com"  # certyfikat weryfikowany po nazwie
    assert "sekret-pat" not in target.connect_url
    assert transport.headers[0]["Authorization"] == "Bearer sekret-pat"


def test_git_allows_self_hosted_lan_but_not_loopback_or_metadata() -> None:
    """`allow_lan` dotyczy WYŁĄCZNIE RFC 1918/ULA — nie luzuje loopbacku ani link-local."""
    transport, resolver = RecordingGitTransport(), CountingResolver("10.10.0.7")
    _git_service(transport, resolver, "https://git.firma.pl").provider_for("gh").list_repositories()
    assert transport.targets[0].connect_url.startswith("https://10.10.0.7/")

    for blocked in (_METADATA, "127.0.0.1", "100.100.100.200", "fec0::1"):
        t2, r2 = RecordingGitTransport(), CountingResolver(blocked)
        with pytest.raises(EgressError):
            _git_service(t2, r2, "https://git.firma.pl").provider_for("gh").list_repositories()
        assert t2.targets == []


@pytest.mark.parametrize(
    "api_base",
    ["https://localhost/api", "https://127.0.0.1/api", "https://git.localhost/api"],
)
def test_git_hard_blocks_loopback_endpoints(api_base: str) -> None:
    """Git NIGDY nie łączy się z loopbackiem — także przez nazwę `localhost`/`*.localhost`."""
    transport, resolver = RecordingGitTransport(), CountingResolver("127.0.0.1")
    service = _git_service(transport, resolver, api_base)
    with pytest.raises(EgressError, match="loopback"):
        service.provider_for("gh").list_repositories()
    assert transport.targets == [] and resolver.calls == []


def test_git_pin_is_fresh_for_every_operation() -> None:
    transport, resolver = RecordingGitTransport(), CountingResolver(_PUBLIC)
    service = _git_service(transport, resolver, "https://api.github.com")
    service.provider_for("gh").list_repositories()
    service.provider_for("gh").list_repositories()
    assert resolver.calls == ["api.github.com", "api.github.com"]


def test_git_denied_egress_does_not_even_resolve_dns() -> None:
    transport, resolver = RecordingGitTransport(), CountingResolver(_PUBLIC)
    service = _git_service(transport, resolver, "https://api.obcy.net")
    with pytest.raises(EgressError, match="Egress zabroniony"):
        service.provider_for("gh").list_repositories()
    assert resolver.calls == [] and transport.targets == []


@pytest.mark.parametrize(
    "api_base",
    [
        "https://gitlab.com/api/v4?x=1",
        "https://gitlab.com/api/v4#frag",
        # PUSTY separator: `urlsplit` zwraca dla nich pusty łańcuch (falsy), więc bramka
        # sprawdzająca WARTOŚCI przepuszczała własny przypadek brzegowy, a gałąź literału IP
        # niosła `api_base` verbatim → żądanie z tokenem szło pod korzeń API.
        "https://gitlab.com/api/v4?",
        "https://gitlab.com/api/v4#",
        "https://192.168.1.10/api/v4#",
    ],
)
def test_git_rejects_api_base_with_query_or_fragment(api_base: str) -> None:
    """Ścieżki zasobów doklejamy do bazy, więc query/fragment w `api_base` dałyby URL
    z dwoma '?' albo ze ścieżką za '#' — odrzucamy fail-closed, bez wysyłania żądania."""
    from husarz.git.errors import GitError

    transport, resolver = RecordingGitTransport(), CountingResolver(_PUBLIC)
    service = GitService(
        secrets=DictSecrets({"env:GH": "sekret-pat"}),
        egress=EgressConfig(default_policy=EgressPolicy.ALLOW),
        transport=transport,
        resolve=resolver,
    )
    service.add(
        GitConnection(
            name="gl", provider=GitProviderKind.GITLAB, api_base=api_base, token_ref="env:GH"
        )
    )
    with pytest.raises(GitError, match="query ani fragmentu"):
        service.provider_for("gl").list_repositories()
    assert transport.targets == [] and resolver.calls == []


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
def test_git_redirect_is_error_not_silent_success(status: int) -> None:
    """`follow_redirects=False` (anty-SSRF) sprawia, że 3xx nie jest sukcesem. Bez jawnej
    gałęzi `list_repositories` zwracało [] („brak repozytoriów"), a `create_pull_request`
    obiekt z pustym URL — operator dostawał „PR utworzony", mimo że żaden nie powstał."""
    from husarz.git.errors import GitError

    class RedirectTransport:
        def __call__(self, method, target, headers, json, timeout):  # type: ignore[no-untyped-def]
            return status, None

    service = GitService(
        secrets=DictSecrets({"env:GH": "sekret-pat"}),
        egress=EgressConfig(allowlist=["github.com"]),
        transport=RedirectTransport(),
        resolve=CountingResolver(_PUBLIC),
    )
    service.add(
        GitConnection(
            name="gh",
            provider=GitProviderKind.GITHUB,
            api_base="https://api.github.com",
            token_ref="env:GH",
        )
    )
    with pytest.raises(GitError, match="przekierowanie"):
        service.provider_for("gh").list_repositories()
    with pytest.raises(GitError, match="przekierowanie"):
        service.provider_for("gh").create_pull_request("o/n", title="T", head="h", base="main")
