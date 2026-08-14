"""Testy API integracji Git: połączenia, repozytoria, tworzenie PR, egress, RBAC."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from husarz.api import create_app
from husarz.config import load_config
from husarz.config.schema import EgressConfig, EgressPolicy
from husarz.git import GitConnection, GitProviderKind, GitService
from husarz.security import AuditLog

pytestmark = pytest.mark.unit


class FakeTransport:
    def __init__(self, responses: dict[tuple[str, str], tuple[int, Any]]) -> None:
        self._responses = responses

    def __call__(self, method, target, headers, json, timeout):  # noqa: ANN001
        for (m, frag), resp in self._responses.items():
            if m == method and frag in target.connect_url:
                return resp
        return 404, {"message": "not found"}


def _fake_resolve(host: str) -> list[str]:
    """Resolver testowy — testy API nie odpytują DNS."""
    return ["140.82.121.6"]


class FakeSecrets:
    def resolve(self, ref: str) -> str | None:
        return "realny-token"


def _github_conn() -> GitConnection:
    return GitConnection(
        name="gh",
        provider=GitProviderKind.GITHUB,
        api_base="https://api.github.com",
        token_ref="env:GH",
        username="acme",
    )


def _client(config_dir: Path, git_service: GitService | None, **kw: Any) -> TestClient:
    config = load_config(config_dir)
    return TestClient(create_app(config, audit=AuditLog(), git_service=git_service, **kw))


def _live_service(responses: dict[tuple[str, str], tuple[int, Any]]) -> GitService:
    svc = GitService(
        secrets=FakeSecrets(),
        egress=EgressConfig(default_policy=EgressPolicy.ALLOW),
        transport=FakeTransport(responses),
        resolve=_fake_resolve,
    )
    svc.add(_github_conn())
    return svc


def test_git_endpoints_404_when_disabled(repo_config_dir: Path) -> None:
    client = _client(repo_config_dir, None)  # brak git_service
    assert client.get("/api/git/connections").status_code == 404


def test_add_and_list_connection_hides_no_secret(repo_config_dir: Path) -> None:
    client = _client(repo_config_dir, GitService())
    body = {
        "name": "gh",
        "provider": "github",
        "api_base": "https://api.github.com",
        "token_ref": "env:GH",
        "username": "acme",
    }
    assert client.post("/api/git/connections", json=body).status_code == 200
    conns = client.get("/api/git/connections").json()
    assert conns[0]["name"] == "gh"
    assert conns[0]["token_ref"] == "env:GH"  # referencja, nie sekret


def test_duplicate_connection_409(repo_config_dir: Path) -> None:
    svc = GitService()
    svc.add(_github_conn())
    client = _client(repo_config_dir, svc)
    body = {
        "name": "gh",
        "provider": "github",
        "api_base": "https://api.github.com",
        "token_ref": "env:GH",
    }
    assert client.post("/api/git/connections", json=body).status_code == 409


def test_list_repos(repo_config_dir: Path) -> None:
    svc = _live_service(
        {
            ("GET", "/user/repos"): (
                200,
                [
                    {
                        "full_name": "acme/app",
                        "default_branch": "main",
                        "private": True,
                        "html_url": "u",
                    }
                ],
            )
        }
    )
    repos = _client(repo_config_dir, svc).get("/api/git/connections/gh/repos").json()
    assert repos[0]["full_name"] == "acme/app"


def test_create_pull_request(repo_config_dir: Path) -> None:
    svc = _live_service(
        {("POST", "/repos/acme/app/pulls"): (201, {"number": 9, "html_url": "u9", "title": "Fix"})}
    )
    client = _client(repo_config_dir, svc)
    body = {"repo": "acme/app", "title": "Fix", "head": "feat", "base": "main", "body": "opis"}
    pr = client.post("/api/git/connections/gh/pull-request", json=body).json()
    assert pr["number"] == 9
    assert pr["url"] == "u9"


def test_egress_blocked_returns_403(repo_config_dir: Path) -> None:
    # Domyślny egress deny-all: api.github.com nie jest dozwolony → 403.
    svc = GitService(secrets=FakeSecrets(), egress=EgressConfig())
    svc.add(_github_conn())
    client = _client(repo_config_dir, svc)
    assert client.get("/api/git/connections/gh/repos").status_code == 403


def test_unknown_connection_repos_404(repo_config_dir: Path) -> None:
    client = _client(repo_config_dir, GitService())
    assert client.get("/api/git/connections/nieznane/repos").status_code == 404


def test_rbac_user_cannot_access_git(repo_config_dir: Path) -> None:
    # Rola 'user' nie ma git:read → 403 (przy włączonym uwierzytelnianiu).
    client = _client(repo_config_dir, GitService(), api_token="s3cret", api_role="user")
    resp = client.get("/api/git/connections", headers={"Authorization": "Bearer s3cret"})
    assert resp.status_code == 403


def test_rbac_user_cannot_write_or_pr(repo_config_dir: Path) -> None:
    client = _client(repo_config_dir, GitService(), api_token="s3cret", api_role="user")
    hdr = {"Authorization": "Bearer s3cret"}
    conn = {
        "name": "x",
        "provider": "github",
        "api_base": "https://api.github.com",
        "token_ref": "env:X",
    }
    assert client.post("/api/git/connections", json=conn, headers=hdr).status_code == 403
    pr = {"repo": "o/n", "title": "t", "head": "h", "base": "main"}
    assert (
        client.post("/api/git/connections/gh/pull-request", json=pr, headers=hdr).status_code == 403
    )


def test_add_connection_rejects_raw_token_422(repo_config_dir: Path) -> None:
    client = _client(repo_config_dir, GitService())
    body = {
        "name": "x",
        "provider": "github",
        "api_base": "https://api.github.com",
        "token_ref": "ghp_RAWSECRET",
    }  # nie-referencja
    assert client.post("/api/git/connections", json=body).status_code == 422


def test_add_connection_rejects_http_422(repo_config_dir: Path) -> None:
    client = _client(repo_config_dir, GitService())
    body = {
        "name": "x",
        "provider": "github",
        "api_base": "http://api.github.com",
        "token_ref": "env:X",
    }
    assert client.post("/api/git/connections", json=body).status_code == 422


def test_pull_request_rejects_bad_repo_422(repo_config_dir: Path) -> None:
    svc = _live_service({})
    body = {"repo": "o/r?x=1", "title": "t", "head": "h", "base": "main"}
    assert (
        _client(repo_config_dir, svc)
        .post("/api/git/connections/gh/pull-request", json=body)
        .status_code
        == 422
    )


def test_provider_auth_error_maps_502(repo_config_dir: Path) -> None:
    svc = _live_service({("GET", "/user/repos"): (401, {"message": "bad token"})})
    assert _client(repo_config_dir, svc).get("/api/git/connections/gh/repos").status_code == 502


def test_delete_connection(repo_config_dir: Path) -> None:
    svc = GitService()
    svc.add(_github_conn())
    client = _client(repo_config_dir, svc)
    assert client.delete("/api/git/connections/gh").status_code == 200
    assert client.get("/api/git/connections").json() == []


def test_gitlab_merge_request_via_api(repo_config_dir: Path) -> None:
    svc = GitService(
        secrets=FakeSecrets(),
        egress=EgressConfig(default_policy=EgressPolicy.ALLOW),
        transport=FakeTransport(
            {("POST", "/merge_requests"): (201, {"iid": 5, "web_url": "w5", "title": "Fix"})}
        ),
        resolve=_fake_resolve,
    )
    svc.add(
        GitConnection(
            name="gl",
            provider=GitProviderKind.GITLAB,
            api_base="https://gitlab.com/api/v4",
            token_ref="env:GL",
        )
    )
    body = {"repo": "grp/app", "title": "Fix", "head": "feat", "base": "main"}
    pr = (
        _client(repo_config_dir, svc).post("/api/git/connections/gl/pull-request", json=body).json()
    )
    assert pr["number"] == 5


def test_pull_request_egress_block_is_audited(repo_config_dir: Path) -> None:
    audit = AuditLog()
    svc = GitService(secrets=FakeSecrets(), egress=EgressConfig())  # deny-all
    svc.add(_github_conn())
    config = load_config(repo_config_dir)
    client = TestClient(create_app(config, audit=audit, git_service=svc))
    body = {"repo": "o/n", "title": "t", "head": "h", "base": "main"}
    assert client.post("/api/git/connections/gh/pull-request", json=body).status_code == 403
    assert any(e.action == "git.pull_request" for e in audit.entries)  # próba audytowana
