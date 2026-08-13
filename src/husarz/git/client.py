"""Klienci dostawców Git (GitHub/GitLab) nad WSTRZYKIWALNYM transportem HTTP.

Transport jest wstrzykiwalny → testy nie wykonują połączeń sieciowych. Każdy dostawca
przechodzi przez bramkę egress (deny-all): host bazy API musi być dozwolony w
``security.egress`` — inaczej ``EgressError`` (suwerenność: bez jawnej zgody nie
łączymy się z WAN). Operacje: lista repozytoriów i utworzenie PR/MR.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable
from urllib.parse import quote

from husarz.config.schema import EgressConfig
from husarz.git.errors import GitAuthError, GitError, GitTransportError
from husarz.git.models import GitConnection, GitProviderKind, PullRequest, Repo
from husarz.router.egress import check_endpoint_allowed

_DEFAULT_TIMEOUT = 30


@runtime_checkable
class GitTransport(Protocol):
    """Warstwa transportu HTTP. Zwraca ``(status_code, sparsowany_json_lub_None)``."""

    def __call__(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        json: dict[str, Any] | None,
        timeout: int,
    ) -> tuple[int, Any]: ...


class HttpxGitTransport:
    """Transport oparty o httpx (import leniwy)."""

    def __init__(self, timeout: int = _DEFAULT_TIMEOUT) -> None:
        self._timeout = timeout

    def __call__(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        json: dict[str, Any] | None,
        timeout: int,
    ) -> tuple[int, Any]:
        try:
            import httpx  # noqa: PLC0415
        except ImportError as exc:  # pragma: no cover - httpx deklarowane w pyproject
            raise GitTransportError("Pakiet 'httpx' nie jest zainstalowany.") from exc
        try:
            response = httpx.request(method, url, headers=headers, json=json, timeout=timeout)
        except httpx.HTTPError as exc:
            raise GitTransportError(f"Błąd HTTP {method} {url}: {exc}") from exc
        try:
            data: Any = response.json()
        except ValueError:
            data = None
        return response.status_code, data


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

    def __init__(self, api_base: str, token: str, transport: GitTransport) -> None:
        self._base = api_base.rstrip("/")
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
            f"{self._base}/user/repos?per_page=100&sort=updated",
            self._headers(),
            None,
            _DEFAULT_TIMEOUT,
        )
        _raise_for_status(status, data, "lista repozytoriów")
        items = data if isinstance(data, list) else []
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
            f"{self._base}/repos/{repo}/pulls",
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

    def __init__(self, api_base: str, token: str, transport: GitTransport) -> None:
        self._base = api_base.rstrip("/")
        self._token = token
        self._t = transport

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token}", "User-Agent": "husarz"}

    def list_repositories(self) -> list[Repo]:  # noqa: D102
        status, data = self._t(
            "GET",
            f"{self._base}/projects?membership=true&per_page=100&order_by=updated_at",
            self._headers(),
            None,
            _DEFAULT_TIMEOUT,
        )
        _raise_for_status(status, data, "lista repozytoriów")
        items = data if isinstance(data, list) else []
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
            f"{self._base}/projects/{project}/merge_requests",
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
) -> GitProvider:
    """Buduje klienta dostawcy dla połączenia. Sprawdza egress bazy API (deny-all).

    Raises:
        EgressError: host bazy API nie jest dozwolony przez politykę egress.
    """
    check_endpoint_allowed(conn.api_base, egress)
    active = transport if transport is not None else HttpxGitTransport()
    if conn.provider is GitProviderKind.GITHUB:
        return GitHubProvider(conn.api_base, token, active)
    return GitLabProvider(conn.api_base, token, active)
