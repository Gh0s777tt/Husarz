"""Testy API kont: rejestracja/logowanie/me/wylogowanie, sesja jako Bearer,
rozliczanie tokenów i limity (HTTP 402)."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from husarz.accounts import AccountService
from husarz.api import create_app
from husarz.config import load_config
from husarz.router import ChatResponse, Usage
from husarz.security import AuditLog

pytestmark = pytest.mark.unit


class EchoRouter:
    """Router testowy: zwraca stałą odpowiedź z zużyciem 7 tokenów (bez sieci)."""

    def complete(self, request, *, agent=None, model=None, tags=None):  # noqa: ANN001
        return ChatResponse(model="husarz-local", content="odp", usage=Usage(total_tokens=7))


def _app_with_accounts(repo_config_dir: Path, service: AccountService) -> TestClient:
    config = load_config(repo_config_dir)
    app = create_app(
        config,
        config_dir=repo_config_dir,
        audit=AuditLog(),
        router=EchoRouter(),
        accounts=service,
        prompts_dir=repo_config_dir.parent / "prompts",
    )
    return TestClient(app)


def test_register_login_me_flow(repo_config_dir: Path) -> None:
    service = AccountService(allow_registration=True, default_role="operator")
    client = _app_with_accounts(repo_config_dir, service)
    reg = client.post("/api/auth/register", json={"username": "ala", "password": "haslo-1234"})
    assert reg.status_code == 200
    token = reg.json()["token"]
    me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    body = me.json()
    assert body["username"] == "ala"
    assert body["role"] == "operator"
    assert body["chat_model"]  # aktywny model czatu pokazany
    assert body["tokens_used"] == 0


def test_registration_disabled_returns_403(repo_config_dir: Path) -> None:
    service = AccountService(allow_registration=False)
    client = _app_with_accounts(repo_config_dir, service)
    resp = client.post("/api/auth/register", json={"username": "ala", "password": "haslo-1234"})
    assert resp.status_code == 403


def test_auth_required_when_accounts_enabled(repo_config_dir: Path) -> None:
    # Włączone konta => uwierzytelnianie ON: chronione endpointy bez tokenu → 401.
    service = AccountService(allow_registration=True)
    client = _app_with_accounts(repo_config_dir, service)
    assert client.get("/api/agents").status_code == 401
    assert client.get("/api/health").status_code == 200  # liveness otwarte


def test_session_token_works_as_bearer(repo_config_dir: Path) -> None:
    service = AccountService(allow_registration=True)
    client = _app_with_accounts(repo_config_dir, service)
    token = client.post(
        "/api/auth/register", json={"username": "ala", "password": "haslo-1234"}
    ).json()["token"]
    hdr = {"Authorization": f"Bearer {token}"}
    assert client.get("/api/agents", headers=hdr).status_code == 200
    assert client.get("/api/agents", headers={"Authorization": "Bearer zly"}).status_code == 401


def test_login_wrong_password_401(repo_config_dir: Path) -> None:
    service = AccountService(allow_registration=True)
    service.create_account("ala", "haslo-1234")
    client = _app_with_accounts(repo_config_dir, service)
    resp = client.post("/api/auth/login", json={"username": "ala", "password": "zle"})
    assert resp.status_code == 401


def test_logout_invalidates_session(repo_config_dir: Path) -> None:
    service = AccountService(allow_registration=True)
    client = _app_with_accounts(repo_config_dir, service)
    token = client.post(
        "/api/auth/register", json={"username": "ala", "password": "haslo-1234"}
    ).json()["token"]
    hdr = {"Authorization": f"Bearer {token}"}
    assert client.post("/api/auth/logout", headers=hdr).status_code == 200
    assert client.get("/api/agents", headers=hdr).status_code == 401


def test_chat_records_token_usage(repo_config_dir: Path) -> None:
    service = AccountService(allow_registration=True)
    client = _app_with_accounts(repo_config_dir, service)
    token = client.post(
        "/api/auth/register", json={"username": "ala", "password": "haslo-1234"}
    ).json()["token"]
    hdr = {"Authorization": f"Bearer {token}"}
    client.post("/api/chat", json={"messages": [{"role": "user", "content": "x"}]}, headers=hdr)
    me = client.get("/api/auth/me", headers=hdr).json()
    assert me["tokens_used"] == 7  # EchoRouter zgłosił 7 tokenów


def test_quota_blocks_with_402(repo_config_dir: Path) -> None:
    service = AccountService(allow_registration=True, default_token_quota=100)
    account = service.create_account("ala", "haslo-1234", role="operator")
    client = _app_with_accounts(repo_config_dir, service)
    token = client.post(
        "/api/auth/login", json={"username": "ala", "password": "haslo-1234"}
    ).json()["token"]
    hdr = {"Authorization": f"Bearer {token}"}
    service.record_usage(account.user_id, 100)  # wyczerp limit
    resp = client.post(
        "/api/chat", json={"messages": [{"role": "user", "content": "x"}]}, headers=hdr
    )
    assert resp.status_code == 402
    me = client.get("/api/auth/me", headers=hdr).json()
    assert me["token_quota"] == 100
    assert me["tokens_remaining"] == 0


def test_viewer_account_cannot_chat(repo_config_dir: Path) -> None:
    service = AccountService(allow_registration=True)
    service.create_account("gosc", "haslo-1234", role="viewer")
    client = _app_with_accounts(repo_config_dir, service)
    token = client.post(
        "/api/auth/login", json={"username": "gosc", "password": "haslo-1234"}
    ).json()["token"]
    hdr = {"Authorization": f"Bearer {token}"}
    resp = client.post(
        "/api/chat", json={"messages": [{"role": "user", "content": "x"}]}, headers=hdr
    )
    assert resp.status_code == 403
