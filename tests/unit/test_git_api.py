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

    def __call__(self, method, url, headers, json, timeout):  # noqa: ANN001
        for (m, frag), resp in self._responses.items():
            if m == method and frag in url:
                return resp
        return 404, {"message": "not found"}


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
