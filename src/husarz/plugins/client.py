"""Konektor MCP: klient zdalnego serwera narzędzi nad WSTRZYKIWALNYM transportem.

Transport (HTTP JSON-RPC 2.0) jest wstrzykiwalny → testy nie wykonują połączeń
sieciowych. Endpoint przechodzi przez bramkę egress z ODWRÓCONĄ polaryzacją względem
Gita: lokalny serwer MCP (loopback) jest GŁÓWNYM przypadkiem i jest dozwolony, ale
adresy wewnętrzne/metadanych (link-local, prywatne, zarezerwowane, multicast,
unspecified) są TWARDO blokowane (anty-SSRF), a hosty publiczne wymagają https +
allowlisty egress (deny-all). Token (opcjonalny) to referencja do sekretu rozwiązywana
leniwie i wysyłana wyłącznie w nagłówku ``Authorization`` (nigdy w URL, nigdy logowana).

``tools/list`` (odkrywanie) oraz ``tools/call`` (wywołanie zdalnego narzędzia) — to drugie
bramkowane deny-by-default przez ``PluginService`` (``allow_call``/``call_allowlist``) i pętlę
agenta (patrz ADR-0015 i ADR-0019). Wynik ``tools/call`` jest NIEZAUFANY: sklejamy tylko bloki
tekstowe, bloki binarne/``resource`` POMIJAMY (zero dereferencji — anti-SSRF-by-proxy).
"""

from __future__ import annotations

import ipaddress
import socket
import time
from collections.abc import Callable, Mapping
from typing import Any, Protocol, runtime_checkable
from urllib.parse import urlparse

from husarz.config.schema import EgressConfig, EgressPolicy, PluginConfig
from husarz.fencing import truncate_utf8
from husarz.plugins.errors import PluginAuthError, PluginError, PluginTransportError
from husarz.plugins.models import RemoteCallResult, RemoteTool
from husarz.router.egress import EgressError

_DEFAULT_TIMEOUT = 30
_DEFAULT_MAX_BYTES = 1_000_000

_IpAddress = ipaddress.IPv4Address | ipaddress.IPv6Address
# Resolver hosta → lista adresów IP (str). Wstrzykiwalny (testy bez DNS).
HostResolver = Callable[[str], list[str]]


def _resolve_ip(host: str) -> _IpAddress | None:
    """Parsuje host jako literał IP; rozwija IPv4-mapped IPv6 (``::ffff:a.b.c.d``).

    Zwraca ``None``, gdy host nie jest literałem IP (nazwa domenowa). Rozwinięcie
    IPv4-mapped domyka bypass, w którym ``::ffff:169.254.169.254`` udawałby adres
    „nie-link-local" (cel metadanych chmury).
    """
    h = host.strip("[]")
    try:
        ip: _IpAddress = ipaddress.ip_address(h)
    except ValueError:
        return None
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        return ip.ipv4_mapped
    return ip


def _ip_is_blocked(ip: _IpAddress) -> bool:
    """True dla adresu wewnętrznego/zarezerwowanego POZA loopbackiem (SSRF).

    Blokuje prywatne (RFC1918/ULA), link-local (169.254 — metadane chmury), multicast,
    zarezerwowane i ``0.0.0.0``. Loopback jest dozwolony (lokalny serwer MCP).
    """
    if ip.is_loopback:
        return False
    return (
        ip.is_private or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified
    )


def _is_loopback_host(host: str) -> bool:
    """True dla ``localhost``/``*.localhost`` oraz literałów loopback (127/8, ::1)."""
    h = host.strip("[]").lower()
    if h == "localhost" or h.endswith(".localhost"):
        return True
    ip = _resolve_ip(h)
    return ip is not None and ip.is_loopback


def _is_blocked_internal(host: str) -> bool:
    """True dla LITERAŁU IP wewnętrznego/zarezerwowanego (poza loopbackiem)."""
    ip = _resolve_ip(host)
    return ip is not None and _ip_is_blocked(ip)


def _default_resolve(host: str) -> list[str]:
    """Rozwiązuje host do adresów IP (A i AAAA). Pusta lista, gdy brak rozwiązania."""
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except OSError:
        return []
    return [str(info[4][0]) for info in infos]


def _validate_mcp_endpoint(
    endpoint: str, egress: EgressConfig, *, resolve: HostResolver | None = None
) -> None:
    """Twarda walidacja endpointu MCP (anty-SSRF, odwrócona polaryzacja vs Git).

    Kolejność: brak userinfo → blok literałów wewnętrznych/metadanych → loopback OK
    (http dozwolony, bo nie wychodzi z hosta) → host publiczny wymaga https + allowlisty
    egress (deny-all). Dla nazwy domenowej dodatkowo **rozwiązuje host i sprawdza KAŻDY
    adres** wobec ``_ip_is_blocked`` (anty-DNS-rebinding; nazwa wskazująca metadane/
    adres wewnętrzny jest blokowana mimo wpisu w allowliście). Nie zamyka pełnego okna
    TOCTOU (bez pinowania IP), ale domyka trywialny rebinding do sieci wewnętrznej.

    Raises:
        PluginError: zły scheme, userinfo w URL, brak hosta, http poza loopbackiem.
        EgressError: host wewnętrzny/metadanych (literał lub po rozwiązaniu) albo
            publiczny spoza allowlisty; brak rozwiązania nazwy (fail-closed).
    """
    parsed = urlparse(endpoint)
    if parsed.scheme not in ("http", "https"):
        raise PluginError("endpoint musi być adresem http(s)://.")
    if parsed.username or parsed.password or "@" in parsed.netloc:
        raise PluginError("endpoint nie może zawierać poświadczeń w URL (userinfo).")
    host = parsed.hostname
    if not host:
        raise PluginError("endpoint nie zawiera hosta.")
    if _is_blocked_internal(host):
        raise EgressError(f"Host '{host}' zablokowany (wewnętrzny/metadanych — SSRF).")
    if _is_loopback_host(host):
        return  # lokalny serwer MCP — główny przypadek; http OK (ruch nie opuszcza hosta)
    if parsed.scheme != "https":
        raise PluginError(
            "endpoint nie-loopback musi być https:// (token nie może lecieć plaintextem)."
        )
    if egress.default_policy is not EgressPolicy.ALLOW and not any(
        host == domain or host.endswith(f".{domain}") for domain in egress.allowlist
    ):
        raise EgressError(
            f"Egress zabroniony dla hosta '{host}' — dodaj go do security.egress.allowlist."
        )
    # Nazwa domenowa: sprawdź KAŻDY rozwiązany adres (anty-DNS-rebinding do wewnętrznych).
    if _resolve_ip(host) is None:
        addresses = (resolve or _default_resolve)(host)
        if not addresses:
            raise EgressError(f"Nie udało się rozwiązać hosta '{host}' (fail-closed).")
        for addr in addresses:
            resolved = _resolve_ip(addr)
            if resolved is not None and _ip_is_blocked(resolved):
                raise EgressError(
                    f"Host '{host}' rozwiązuje się na adres wewnętrzny/metadanych "
                    f"({addr}) — SSRF/DNS-rebinding."
                )


@runtime_checkable
class PluginTransport(Protocol):
    """Warstwa transportu MCP (POST JSON-RPC). Zwraca ``(status, sparsowany_json_lub_None)``.

    ``max_bytes`` egzekwuje twardy limit ciała PODCZAS odczytu (ochrona OOM przed
    złośliwym/przejętym serwerem MCP) — nie po sparsowaniu.
    """

    def __call__(
        self,
        url: str,
        headers: dict[str, str],
        json: dict[str, Any],
        timeout: int,
        max_bytes: int,
    ) -> tuple[int, Any]: ...


class HttpxPluginTransport:
    """Transport oparty o httpx (import leniwy). TLS ``verify=True`` jawnie, na sztywno.

    Ciało czytane strumieniowo z twardym sufitem ``max_bytes`` (przerwanie po
    przekroczeniu, przed ``json.loads``). ``follow_redirects=False`` (anty-SSRF-redirect).
    Dodatkowo bezwzględny **deadline wall-clock** (``timeout``) na całą pętlę odczytu —
    ochrona przed „slow-drip" (serwer sączący bajty w nieskończoność blokowałby wątek
    puli), bo per-read timeout httpx resetuje się przy każdym chunku.
    """

    def __call__(
        self,
        url: str,
        headers: dict[str, str],
        json: dict[str, Any],
        timeout: int,
        max_bytes: int,
    ) -> tuple[int, Any]:
        try:
            import httpx  # noqa: PLC0415
        except ImportError as exc:  # pragma: no cover - httpx deklarowane w pyproject
            raise PluginTransportError("Pakiet 'httpx' nie jest zainstalowany.") from exc
        import json as _json  # noqa: PLC0415

        buffer = bytearray()
        deadline = time.monotonic() + timeout
        try:
            with httpx.stream(
                "POST",
                url,
                headers=headers,
                json=json,
                timeout=timeout,
                follow_redirects=False,
                verify=True,
            ) as response:
                for chunk in response.iter_bytes():
                    buffer += chunk
                    if len(buffer) > max_bytes:
                        raise PluginTransportError(
                            "Odpowiedź serwera wtyczki przekracza limit rozmiaru."
                        )
                    if time.monotonic() > deadline:
                        raise PluginTransportError(
                            "Przekroczono całkowity limit czasu odpowiedzi serwera wtyczki."
                        )
                status = response.status_code
        except PluginTransportError:
            raise
        except httpx.HTTPError as exc:
            # Komunikat GENERYCZNY — bez URL/wnętrzności httpx (nie wyciekają do audytu/API).
            raise PluginTransportError("Błąd transportu do serwera wtyczki.") from exc
        try:
            data: Any = _json.loads(bytes(buffer)) if buffer else None
        except ValueError:
            data = None
        return status, data


def _raise_for_status(status: int, action: str) -> None:
    """Mapuje kod HTTP na wyjątek wtyczki. NIE echuje treści serwera (mniej zaufany)."""
    if status in (401, 403):
        raise PluginAuthError(f"{action}: brak autoryzacji u serwera wtyczki (HTTP {status}).")
    if status >= 400:
        raise PluginError(f"{action}: serwer wtyczki zwrócił HTTP {status}.")


def _parse_call_result(result: Any, *, max_bytes: int) -> RemoteCallResult:
    """Parsuje NIEZAUFANY wynik ``tools/call``. Bez ``getattr``, bez dereferencji URI/resource.

    Skleja wyłącznie bloki ``type=text``; bloki binarne/``resource``/nieznane zastępuje
    krótkim placeholderem (NIGDY nie pobiera bajtów — transport wołany dokładnie raz).
    Tekst przycinany do ``max_bytes`` UTF-8 (config-driven cap, defense-in-depth).
    """
    if not isinstance(result, dict):
        return RemoteCallResult(text="", is_error=True)  # fail-safe: nieznany kształt
    is_error = bool(result.get("isError"))
    raw = result.get("content")
    parts: list[str] = []
    if isinstance(raw, list):
        for item in raw:
            if not isinstance(item, dict):
                continue
            item_type = str(item.get("type") or "")
            if item_type == "text":
                parts.append(str(item.get("text") or ""))
            else:
                parts.append(f"[pominięto blok typu '{item_type or 'nieznany'}']")
    text, _ = truncate_utf8("\n".join(parts), max_bytes)
    return RemoteCallResult(text=text, is_error=is_error)


class McpClient:
    """Klient MCP nad ``PluginTransport`` (JSON-RPC 2.0): ``tools/list`` i ``tools/call``."""

    def __init__(
        self,
        endpoint: str,
        token: str,
        transport: PluginTransport,
        *,
        timeout: int = _DEFAULT_TIMEOUT,
        max_output_bytes: int = _DEFAULT_MAX_BYTES,
    ) -> None:
        self._url = endpoint
        self._token = token
        self._t = transport
        self._timeout = timeout
        self._max_bytes = max_output_bytes
        self._id = 0

    def _headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "husarz",
        }
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return headers

    def _rpc(self, method: str, params: dict[str, Any]) -> Any:
        self._id += 1
        envelope = {"jsonrpc": "2.0", "id": self._id, "method": method, "params": params}
        status, data = self._t(self._url, self._headers(), envelope, self._timeout, self._max_bytes)
        _raise_for_status(status, method)
        if isinstance(data, dict) and data.get("error"):
            # Błąd JSON-RPC — nie ujawniamy wnętrzności serwera (tylko kategoria).
            raise PluginError(f"Serwer wtyczki zwrócił błąd metody '{method}'.")
        return data.get("result") if isinstance(data, dict) else None

    def list_tools(self) -> list[RemoteTool]:
        """Odkrywa narzędzia zdalnego serwera (``tools/list``). Wynik NIEZAUFANY."""
        result = self._rpc("tools/list", {})
        raw = result.get("tools") if isinstance(result, dict) else None
        items = [t for t in raw if isinstance(t, dict)] if isinstance(raw, list) else []
        tools: list[RemoteTool] = []
        for item in items:
            name = str(item.get("name") or "")
            if not name:
                continue
            tools.append(RemoteTool(name=name, description=str(item.get("description") or "")))
        return tools

    def call_tool(self, name: str, arguments: Mapping[str, Any]) -> RemoteCallResult:
        """Wywołuje zdalne narzędzie (``tools/call``). Wynik NIEZAUFANY (``_parse_call_result``).

        ``arguments`` przekazywane VERBATIM jako dane (żadne referencje ``env:/file:/...`` NIE
        są rozwiązywane — sekret modelu nie jest eksfiltrowany). Błąd protokołu JSON-RPC →
        ``PluginError``; aplikacyjne ``isError`` → ``RemoteCallResult.is_error`` (nie wyjątek).
        """
        result = self._rpc("tools/call", {"name": name, "arguments": dict(arguments)})
        return _parse_call_result(result, max_bytes=self._max_bytes)


def build_connector(
    plugin: PluginConfig,
    token: str,
    egress: EgressConfig,
    *,
    transport: PluginTransport | None = None,
    resolve: HostResolver | None = None,
) -> McpClient:
    """Buduje klienta MCP dla wtyczki. Waliduje endpoint + egress PRZED połączeniem.

    Raises:
        EgressError: host endpointu wewnętrzny (literał lub po rozwiązaniu) albo publiczny
            spoza allowlisty; nierozwiązywalna nazwa (fail-closed).
        PluginError: endpoint nie jest poprawnym http(s) bez userinfo (lub http poza loopbackiem).
    """
    _validate_mcp_endpoint(plugin.endpoint, egress, resolve=resolve)
    active = transport if transport is not None else HttpxPluginTransport()
    return McpClient(
        plugin.endpoint,
        token,
        active,
        timeout=plugin.timeout_seconds,
        max_output_bytes=plugin.max_output_bytes,
    )
