"""Klienci dostawców Git (GitHub/GitLab) nad WSTRZYKIWALNYM transportem HTTP.

Transport jest wstrzykiwalny → testy nie wykonują połączeń sieciowych. Każdy dostawca
przechodzi przez bramkę egress (deny-all): host bazy API musi być dozwolony w
``security.egress`` — inaczej ``EgressError`` (suwerenność: bez jawnej zgody nie
łączymy się z WAN). Operacje: lista repozytoriów i utworzenie PR/MR.

Klasyfikacja hosta i **pinowanie IP** są współdzielone z ``web`` i konektorem MCP
(moduł ``husarz.ssrf``, ADR-0020): nazwa rozwiązywana DOKŁADNIE RAZ, każdy adres
sprawdzany, jeden przypinany — transport łączy się z literałem IP, a ``Host`` i SNI
(weryfikacja certyfikatu) idą po ORYGINALNEJ nazwie.

Kod wrażliwy (sieć + token z prawem ZAPISU do repozytoriów): patrz
``docs/BEZPIECZENSTWO.md``. Polaryzacja tej ścieżki: loopback ZABRONIONY (Git nigdy nie
jest usługą lokalną Husarza), ale prywatna sieć operatora DOZWOLONA (``allow_lan``) —
samodzielnie hostowany GitLab pod adresem RFC 1918 to legalny scenariusz suwerenności.
"""

from __future__ import annotations

import time
from typing import Any, Protocol, runtime_checkable
from urllib.parse import quote, urlparse, urlsplit

from husarz.config.schema import EgressConfig, EgressPolicy
from husarz.git.errors import GitAuthError, GitError, GitTransportError
from husarz.git.models import GitConnection, GitProviderKind, PullRequest, Repo
from husarz.router.egress import EgressError
from husarz.ssrf import (
    HostResolver,
    PinnedTarget,
    default_resolve,
    is_blocked_address,
    is_loopback_host,
    is_loopback_name,
    parse_ip_literal,
    pin_fields,
    resolve_and_pin,
    safe_port,
)

_DEFAULT_TIMEOUT = 30
# Twardy sufit odpowiedzi dostawcy (anty-OOM) i rozmiar jednej iteracji odczytu.
_DEFAULT_MAX_BYTES = 5_000_000
_READ_CHUNK_BYTES = 64 * 1024


def _endpoint_target(
    api_base: str, egress: EgressConfig, *, resolve: HostResolver | None = None
) -> PinnedTarget:
    """Waliduje bazę API dostawcy Git (anty-SSRF) i zwraca cel połączenia z PINEM IP.

    Kolejność bram — odmowa nie kosztuje nawet zapytania DNS:

    1. ``https`` (token nie może lecieć plaintextem) i brak userinfo w URL,
    2. poprawny port,
    3. loopback (literał, ``localhost``, ``*.localhost``) → twardy blok,
    4. literał adresu wewnętrznego/metadanych → twardy blok,
    5. allowlista egress (deny-all) — Git ŚWIADOMIE nie korzysta ze skrótu „lokalne = zawsze
       dozwolone" z :func:`husarz.router.egress.check_endpoint_allowed` (bezpieczny dla
       lokalnego Ollamy, nie dla ścieżki niosącej token z prawem zapisu do repozytoriów),
    6. nazwa domenowa → rozwiąż RAZ i **przypnij** (domknięcie okna TOCTOU rebindingu).

    W odróżnieniu od ``web`` i konektora MCP ta ścieżka używa ``allow_lan=True``: samodzielnie
    hostowany GitLab pod adresem RFC 1918/ULA to legalny scenariusz suwerenności. Luz dotyczy
    WYŁĄCZNIE sieci prywatnych — loopback, link-local (metadane chmury), CGNAT i tunele
    osadzające IPv4 pozostają twardo zablokowane.

    Args:
        api_base: baza API z połączenia (np. ``https://api.github.com``).
        egress: globalna polityka egress (deny-all).
        resolve: resolver DNS (wstrzykiwalny; ``None`` → stdlib).

    Returns:
        ``PinnedTarget``: połączenie wprost (literał) albo z przypiętym IP.

    Raises:
        GitError: zły schemat, userinfo w URL, brak hosta.
        EgressError: host wewnętrzny/loopback, zły port, host spoza allowlisty,
            nierozwiązywalna nazwa albo nazwa wskazująca adres wewnętrzny (fail-closed).
    """
    active_resolve = resolve if resolve is not None else default_resolve
    parsed = urlparse(api_base)
    if parsed.scheme != "https":
        raise GitError("api_base musi być adresem https:// (token nie może lecieć plaintextem).")
    if parsed.username or parsed.password or "@" in parsed.netloc:
        raise GitError("api_base nie może zawierać poświadczeń w URL (userinfo).")
    host = parsed.hostname
    if not host:
        raise GitError("api_base nie zawiera hosta.")
    safe_port(urlsplit(api_base), api_base)
    if is_loopback_host(host) or is_loopback_name(host):
        raise EgressError(f"Host '{host}' zablokowany dla Git (loopback — SSRF).")
    literal = parse_ip_literal(host)
    if literal is not None and is_blocked_address(literal, allow_loopback=False, allow_lan=True):
        raise EgressError(
            f"Host '{host}' zablokowany dla Git (loopback/link-local/metadata — SSRF)."
        )
    if egress.default_policy is not EgressPolicy.ALLOW and not any(
        host == domain or host.endswith(f".{domain}") for domain in egress.allowlist
    ):
        raise EgressError(
            f"Egress zabroniony dla hosta '{host}' — dodaj go do security.egress.allowlist."
        )
    if literal is not None:
        return PinnedTarget.direct(api_base)  # nie ma nazwy, więc nie ma czego przypinać
    pinned = resolve_and_pin(host, allow_loopback=False, resolve=active_resolve, allow_lan=True)
    return pin_fields(api_base, pinned)


def _request_target(base: PinnedTarget, path: str) -> PinnedTarget:
    """Cel dla POJEDYNCZEGO żądania: ``path`` doklejona do bazy, pin/Host/SNI bez zmian.

    Args:
        base: cel bazy API (wynik :func:`_endpoint_target`).
        path: ścieżka z wiodącym ``/`` (wraz z query).

    Returns:
        Nowy ``PinnedTarget`` wskazujący konkretny zasób — wciąż po przypiętym adresie.
    """
    return PinnedTarget(
        connect_url=f"{base.connect_url.rstrip('/')}{path}",
        host_header=base.host_header,
        sni_hostname=base.sni_hostname,
        pinned_ip=base.pinned_ip,
    )


@runtime_checkable
class GitTransport(Protocol):
    """Warstwa transportu HTTP. Zwraca ``(status_code, sparsowany_json_lub_None)``.

    Przyjmuje ``PinnedTarget`` (a NIE goły URL) celowo: pin jest częścią kontraktu, więc
    implementacja nie może go pominąć i rozwiązać nazwy ponownie (okno TOCTOU).
    """

    def __call__(
        self,
        method: str,
        target: PinnedTarget,
        headers: dict[str, str],
        json: dict[str, Any] | None,
        timeout: int,
    ) -> tuple[int, Any]: ...


class HttpxGitTransport:
    """Transport oparty o httpx (import leniwy). TLS ``verify=True`` jawnie, na sztywno.

    Łączy się z ``target.connect_url`` (literał IP dla nazw domenowych), a nagłówek ``Host``
    i SNI/weryfikacja certyfikatu idą po ORYGINALNEJ nazwie — pin nie degraduje TLS.
    ``trust_env=False``: zmienne ``HTTP(S)_PROXY`` ze środowiska nie mogą przekierować
    przypiętego połączenia (egress pochodzi z configu, nie ze środowiska procesu).
    ``follow_redirects=False`` — przekierowanie omijałoby walidację i pin.

    Ciało czytane strumieniowo z twardym sufitem ``max_bytes`` i bezwzględnym deadline'em
    wall-clock (anty-OOM i anty-„slow-drip"; wcześniej ``response.json()`` wciągało całą
    odpowiedź dostawcy bez limitu). Komunikat błędu jest GENERYCZNY — bez URL-a i wnętrzności
    httpx, które trafiłyby do audytu/API (parytet z konektorem MCP).
    """

    def __init__(
        self, timeout: int = _DEFAULT_TIMEOUT, *, max_bytes: int = _DEFAULT_MAX_BYTES
    ) -> None:
        self._timeout = timeout
        self._max_bytes = max_bytes

    def __call__(
        self,
        method: str,
        target: PinnedTarget,
        headers: dict[str, str],
        json: dict[str, Any] | None,
        timeout: int,
    ) -> tuple[int, Any]:
        try:
            import httpx  # noqa: PLC0415
        except ImportError as exc:  # pragma: no cover - httpx deklarowane w pyproject
            raise GitTransportError("Pakiet 'httpx' nie jest zainstalowany.") from exc
        import json as _json  # noqa: PLC0415

        sent_headers = dict(headers)
        if target.host_header is not None:
            sent_headers["Host"] = target.host_header
        extensions: dict[str, Any] = {}
        if target.sni_hostname is not None:
            # httpcore używa tego jako ``server_hostname`` w start_tls → certyfikat jest
            # weryfikowany wobec NAZWY, mimo że łączymy się z literałem IP.
            extensions["sni_hostname"] = target.sni_hostname

        buffer = bytearray()
        deadline = time.monotonic() + timeout
        try:
            with (
                httpx.Client(
                    timeout=timeout, follow_redirects=False, verify=True, trust_env=False
                ) as client,
                client.stream(
                    method,
                    target.connect_url,
                    headers=sent_headers,
                    json=json,
                    extensions=extensions,
                ) as response,
            ):
                for chunk in response.iter_bytes(chunk_size=_READ_CHUNK_BYTES):
                    buffer += chunk
                    if len(buffer) > self._max_bytes:
                        raise GitTransportError("Odpowiedź dostawcy przekracza limit rozmiaru.")
                    if time.monotonic() > deadline:
                        raise GitTransportError("Przekroczono limit czasu odpowiedzi dostawcy.")
                status = response.status_code
        except GitTransportError:
            raise
        except httpx.HTTPError as exc:
            raise GitTransportError("Błąd transportu do dostawcy Git.") from exc
        try:
            data: Any = _json.loads(bytes(buffer)) if buffer else None
        except ValueError:
            data = None
        return status, data


def _raise_for_status(status: int, data: Any, action: str) -> None:
    """Mapuje kod odpowiedzi na wyjątek Git (401/403 → auth; 4xx/5xx → GitError)."""
    if status in (401, 403):
        raise GitAuthError(f"{action}: brak autoryzacji u dostawcy (HTTP {status}).")
    if status >= 400:
        message = ""
        if isinstance(data, dict):
            message = str(data.get("message") or data.get("error") or "")
        raise GitError(f"{action}: dostawca zwrócił HTTP {status}. {message}".strip())


@runtime_checkable
class GitProvider(Protocol):
    """Operacje na repozytoriach dostawcy Git."""

    def list_repositories(self) -> list[Repo]: ...

    def create_pull_request(
        self, repo: str, *, title: str, head: str, base: str, body: str = ""
    ) -> PullRequest: ...


class GitHubProvider:
    """Klient GitHub (REST v3)."""

    def __init__(self, target: PinnedTarget, token: str, transport: GitTransport) -> None:
        self._target = target
        self._token = token
        self._t = transport

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "husarz",
        }

    def list_repositories(self) -> list[Repo]:  # noqa: D102 - patrz Protocol
        status, data = self._t(
            "GET",
            _request_target(self._target, "/user/repos?per_page=100&sort=updated"),
            self._headers(),
            None,
            _DEFAULT_TIMEOUT,
        )
        _raise_for_status(status, data, "lista repozytoriów")
        items = [r for r in data if isinstance(r, dict)] if isinstance(data, list) else []
        return [
            Repo(
                full_name=str(r.get("full_name", "")),
                default_branch=str(r.get("default_branch") or "main"),
                private=bool(r.get("private")),
                url=str(r.get("html_url", "")),
            )
            for r in items
        ]

    def create_pull_request(
        self, repo: str, *, title: str, head: str, base: str, body: str = ""
    ) -> PullRequest:  # noqa: D102
        status, data = self._t(
            "POST",
            # Kodowanie ścieżki repo (zachowanie '/') — koniec wstrzykiwania '?','#' do URL.
            _request_target(self._target, f"/repos/{quote(repo, safe='/')}/pulls"),
            self._headers(),
            {"title": title, "head": head, "base": base, "body": body},
            _DEFAULT_TIMEOUT,
        )
        _raise_for_status(status, data, "utworzenie PR")
        payload = data if isinstance(data, dict) else {}
        return PullRequest(
            number=payload.get("number"),
            url=str(payload.get("html_url", "")),
            title=str(payload.get("title", title)),
        )


class GitLabProvider:
    """Klient GitLab (REST v4). ``repo`` to ścieżka ``grupa/projekt`` (URL-encoded)."""

    def __init__(self, target: PinnedTarget, token: str, transport: GitTransport) -> None:
        self._target = target
        self._token = token
        self._t = transport

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token}", "User-Agent": "husarz"}

    def list_repositories(self) -> list[Repo]:  # noqa: D102
        status, data = self._t(
            "GET",
            _request_target(
                self._target, "/projects?membership=true&per_page=100&order_by=updated_at"
            ),
            self._headers(),
            None,
            _DEFAULT_TIMEOUT,
        )
        _raise_for_status(status, data, "lista repozytoriów")
        items = [r for r in data if isinstance(r, dict)] if isinstance(data, list) else []
        return [
            Repo(
                full_name=str(r.get("path_with_namespace", "")),
                default_branch=str(r.get("default_branch") or "main"),
                private=str(r.get("visibility", "")) != "public",
                url=str(r.get("web_url", "")),
            )
            for r in items
        ]

    def create_pull_request(
        self, repo: str, *, title: str, head: str, base: str, body: str = ""
    ) -> PullRequest:  # noqa: D102
        project = quote(repo, safe="")  # ścieżka projektu URL-encoded (wymóg GitLab API)
        status, data = self._t(
            "POST",
            _request_target(self._target, f"/projects/{project}/merge_requests"),
            self._headers(),
            {"source_branch": head, "target_branch": base, "title": title, "description": body},
            _DEFAULT_TIMEOUT,
        )
        _raise_for_status(status, data, "utworzenie MR")
        payload = data if isinstance(data, dict) else {}
        return PullRequest(
            number=payload.get("iid"),
            url=str(payload.get("web_url", "")),
            title=str(payload.get("title", title)),
        )


def build_provider(
    conn: GitConnection,
    token: str,
    egress: EgressConfig,
    *,
    transport: GitTransport | None = None,
    resolve: HostResolver | None = None,
) -> GitProvider:
    """Buduje klienta dostawcy: walidacja bazy API + egress + **pin IP** przed połączeniem.

    Klient jest budowany PER OPERACJĘ (``GitService._provider``), więc pin jest świeży dla
    każdego wywołania i nie starzeje się między żądaniami.

    Raises:
        EgressError: host bazy API wewnętrzny/loopback, niedozwolony przez politykę egress
            albo nierozwiązywalny/wskazujący adres wewnętrzny (fail-closed).
        GitError: api_base nie jest poprawnym https bez poświadczeń w URL.
    """
    target = _endpoint_target(conn.api_base, egress, resolve=resolve)
    active = transport if transport is not None else HttpxGitTransport()
    if conn.provider is GitProviderKind.GITHUB:
        return GitHubProvider(target, token, active)
    return GitLabProvider(target, token, active)
