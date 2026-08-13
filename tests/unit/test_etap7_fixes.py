"""Regresje z adwersaryjnego przeglądu Etapu 7 (konta): najmniejsze uprawnienia,
anty-brute-force, walidacja ról, atomowy zapis, pusty token, most config→konta, useradd."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from husarz.accounts import AccountService
from husarz.accounts.builder import build_account_service
from husarz.accounts.errors import AccountLockedError
from husarz.accounts.store import FileAccountStore
from husarz.api import create_app
from husarz.config import load_config
from husarz.config.schema import AuthConfig
from husarz.launcher.cli import (
    _accounts_enabled,
    _build_accounts,
    _cmd_up,
    _cmd_useradd,
    build_parser,
)
from husarz.security import AuditLog

pytestmark = pytest.mark.unit


# --- Walidacja ról i seedu (finding: brak walidacji) ------------------------


def test_config_rejects_unknown_api_role() -> None:
    with pytest.raises(ValidationError):
        AuthConfig(api_role="nieistniejaca")


def test_config_rejects_unknown_default_user_role() -> None:
    with pytest.raises(ValidationError):
        AuthConfig(default_user_role="nieistniejaca")


def test_config_rejects_partial_seed() -> None:
    with pytest.raises(ValidationError):
        AuthConfig(seed_admin_username="hetman")  # brak seed_admin_password_ref


# --- Najmniejsze uprawnienia (finding: default operator) --------------------


def test_default_registration_role_is_user() -> None:
    service = AccountService(allow_registration=True)  # bez jawnej roli
    account = service.register("ala", "haslo-1234")
    assert account.role == "user"  # nie 'operator'


# --- Anty-brute-force (finding: brak lockoutu) ------------------------------


def test_login_lockout_after_max_attempts() -> None:
    service = AccountService(allow_registration=True, login_max_attempts=3)
    service.create_account("ala", "haslo-1234")
    for _ in range(3):
        with pytest.raises(Exception):  # noqa: B017 - AuthenticationError
            service.authenticate("ala", "zle")
    with pytest.raises(AccountLockedError):
        service.authenticate("ala", "haslo-1234")  # nawet poprawne — konto zablokowane


def test_lockout_window_expires(monkeypatch: pytest.MonkeyPatch) -> None:
    now = {"t": datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)}
    service = AccountService(
        allow_registration=True,
        login_max_attempts=2,
        login_lockout_seconds=60,
        clock=lambda: now["t"],
    )
    service.create_account("ala", "haslo-1234")
    for _ in range(2):
        with pytest.raises(Exception):  # noqa: B017
            service.authenticate("ala", "zle")
    now["t"] = datetime(2026, 1, 1, 12, 2, 0, tzinfo=UTC)  # +2 min > okno 60 s
    account = service.authenticate("ala", "haslo-1234")  # blokada wygasła
    assert account.username == "ala"


def test_api_login_lockout_returns_429(repo_config_dir: Path) -> None:
    service = AccountService(allow_registration=True, login_max_attempts=2)
    service.create_account("ala", "haslo-1234")
    config = load_config(repo_config_dir)
    client = TestClient(create_app(config, audit=AuditLog(), accounts=service))
    for _ in range(2):
        client.post("/api/auth/login", json={"username": "ala", "password": "zle"})
    r = client.post("/api/auth/login", json={"username": "ala", "password": "haslo-1234"})
    assert r.status_code == 429


# --- Pusty token maszynowy (finding: 'Bearer ' pasuje) ----------------------


def test_empty_bearer_rejected_when_token_set(repo_config_dir: Path) -> None:
    config = load_config(repo_config_dir)
    client = TestClient(create_app(config, audit=AuditLog(), api_token="realny-token"))
    assert client.get("/api/agents", headers={"Authorization": "Bearer "}).status_code == 401


# --- Sesje: sprzątanie / limit (finding: brak sweepu) -----------------------


def test_session_sweep_bounds_growth(monkeypatch: pytest.MonkeyPatch) -> None:
    now = {"t": datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)}
    service = AccountService(
        allow_registration=True, session_ttl_seconds=60, clock=lambda: now["t"]
    )
    service.register("ala", "haslo-1234")
    service.login("ala", "haslo-1234")
    now["t"] = datetime(2026, 1, 1, 12, 5, 0, tzinfo=UTC)  # wygasła
    service.login("ala", "haslo-1234")  # logowanie sprząta wygasłe
    assert len(service._sessions) == 1  # brak narastania


# --- Trwały magazyn: atomowy zapis (finding: nieatomowy write) --------------


def test_file_store_atomic_leaves_no_tmp(tmp_path: Path) -> None:
    path = tmp_path / "accounts.json"
    service = AccountService(FileAccountStore(path), allow_registration=True)
    service.register("ala", "haslo-1234")
    service.register("ola", "haslo-5678")
    assert path.exists()
    assert not (tmp_path / "accounts.json.tmp").exists()  # brak śmiecia po os.replace
    reloaded = AccountService(FileAccountStore(path), allow_registration=True)
    assert {a.username for a in reloaded.list_accounts()} == {"ala", "ola"}


# --- Most config→konta i seed z ENV (finding: luka pokrycia) ----------------


def test_accounts_enabled_and_built_from_env(
    repo_config_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HUSARZ_SECURITY__AUTH__ACCOUNTS_PATH", str(tmp_path / "acc.json"))
    monkeypatch.setenv("HUSARZ_SECURITY__AUTH__SEED_ADMIN_USERNAME", "hetman")
    monkeypatch.setenv("HUSARZ_SECURITY__AUTH__SEED_ADMIN_PASSWORD_REF", "env:HUSARZ_ADMIN_PW")
    monkeypatch.setenv("HUSARZ_ADMIN_PW", "silne-haslo-admina")
    config = load_config(repo_config_dir)
    assert _accounts_enabled(config) is True
    service = _build_accounts(config)
    assert service is not None
    account, _ = service.login("hetman", "silne-haslo-admina")  # seed zadziałał
    assert account.role == "admin"


# --- Launcher: fail-closed spełnione przez konta (finding: luka pokrycia) ----


def test_up_with_accounts_allows_non_loopback(
    repo_config_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HUSARZ_SECURITY__AUTH__ALLOW_REGISTRATION", "true")
    import uvicorn

    served: dict[str, object] = {}
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: served.update(app=app))
    prompts = repo_config_dir.parent / "prompts"
    args = build_parser().parse_args(
        [
            "up",
            "--config",
            str(repo_config_dir),
            "--host",
            "0.0.0.0",  # noqa: S104 - konta spełniają wymóg auth, więc dozwolone
            "--prompts",
            str(prompts),
        ]
    )
    assert _cmd_up(args) == 0  # konta = uwierzytelnianie → brak odmowy
    assert "app" in served


# --- husarz useradd (dostęp „dla wybranych") --------------------------------


def test_useradd_creates_persistent_account(
    repo_config_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HUSARZ_SECURITY__AUTH__ACCOUNTS_PATH", str(tmp_path / "acc.json"))
    monkeypatch.setenv("HUSARZ_NEW_USER_PASSWORD", "silne-haslo-1234")
    args = build_parser().parse_args(
        ["useradd", "--config", str(repo_config_dir), "--username", "gosc", "--role", "operator"]
    )
    assert _cmd_useradd(args) == 0
    service = build_account_service(load_config(repo_config_dir).security.auth)
    account, _ = service.login("gosc", "silne-haslo-1234")
    assert account.role == "operator"


def test_useradd_requires_accounts_path(repo_config_dir: Path) -> None:
    args = build_parser().parse_args(
        ["useradd", "--config", str(repo_config_dir), "--username", "gosc"]
    )
    assert _cmd_useradd(args) == 1  # brak accounts_path → odmowa
