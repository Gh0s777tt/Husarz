"""Testy warstwy kont: hasła (scrypt), rejestracja/logowanie, sesje, limity, magazyn."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from husarz.accounts import (
    AccountService,
    FileAccountStore,
    hash_password,
    verify_password,
)
from husarz.accounts.builder import build_account_service
from husarz.accounts.errors import (
    AuthenticationError,
    QuotaExceededError,
    RegistrationDisabledError,
)
from husarz.accounts.store import AccountError
from husarz.config.schema import AuthConfig

pytestmark = pytest.mark.unit


# --- Hasła ------------------------------------------------------------------


def test_password_hash_is_not_plaintext_and_verifies() -> None:
    h = hash_password("Sekretne!123")
    assert "Sekretne!123" not in h
    assert h.startswith("scrypt$")
    assert verify_password("Sekretne!123", h) is True
    assert verify_password("złe hasło", h) is False


def test_password_hash_uses_random_salt() -> None:
    assert hash_password("to samo") != hash_password("to samo")


def test_verify_rejects_malformed_encoding() -> None:
    assert verify_password("x", "nonsense") is False
    assert verify_password("x", "bcrypt$1$2$3$a$b") is False


def test_hash_rejects_empty_password() -> None:
    with pytest.raises(ValueError):
        hash_password("")


# --- Rejestracja i tworzenie kont -------------------------------------------


def test_registration_disabled_by_default() -> None:
    service = AccountService()
    with pytest.raises(RegistrationDisabledError):
        service.register("ala", "haslo-123")


def test_registration_when_enabled() -> None:
    service = AccountService(allow_registration=True, default_role="viewer")
    account = service.register("ala", "haslo-123")
    assert account.username == "ala"
    assert account.role == "viewer"


def test_duplicate_username_rejected_case_insensitive() -> None:
    service = AccountService(allow_registration=True)
    service.register("Ala", "haslo-123")
    with pytest.raises(AccountError):
        service.register("ala", "inne-haslo")


# --- Logowanie i sesje ------------------------------------------------------


def test_login_resolves_session() -> None:
    service = AccountService(allow_registration=True)
    service.register("ala", "haslo-123")
    account, token = service.login("ala", "haslo-123")
    resolved = service.resolve_session(token)
    assert resolved is not None
    assert resolved.user_id == account.user_id


def test_authenticate_wrong_credentials() -> None:
    service = AccountService(allow_registration=True)
    service.register("ala", "haslo-123")
    with pytest.raises(AuthenticationError):
        service.authenticate("ala", "błędne")
    with pytest.raises(AuthenticationError):
        service.authenticate("nieznany", "cokolwiek")


def test_session_expires(monkeypatch: pytest.MonkeyPatch) -> None:
    now = {"t": datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)}
    service = AccountService(
        allow_registration=True, clock=lambda: now["t"], session_ttl_seconds=60
    )
    service.register("ala", "haslo-123")
    _, token = service.login("ala", "haslo-123")
    assert service.resolve_session(token) is not None  # w oknie
    now["t"] = datetime(2026, 1, 1, 12, 2, 0, tzinfo=UTC)  # +2 min > TTL 60 s
    assert service.resolve_session(token) is None  # wygasła


def test_logout_invalidates_session() -> None:
    service = AccountService(allow_registration=True)
    service.register("ala", "haslo-123")
    _, token = service.login("ala", "haslo-123")
    service.logout(token)
    assert service.resolve_session(token) is None


# --- Limity i zużycie tokenów -----------------------------------------------


def test_quota_enforced_after_usage() -> None:
    service = AccountService(allow_registration=True, default_token_quota=100)
    account = service.register("ala", "haslo-123")
    service.check_quota(account.user_id)  # 0/100 — OK
    service.record_usage(account.user_id, 100)
    with pytest.raises(QuotaExceededError):
        service.check_quota(account.user_id)
    assert service.get(account.user_id).tokens_used == 100


def test_no_quota_means_unlimited() -> None:
    service = AccountService(allow_registration=True)  # default_token_quota=None
    account = service.register("ala", "haslo-123")
    service.record_usage(account.user_id, 10_000_000)
    service.check_quota(account.user_id)  # brak limitu → brak wyjątku


# --- Magazyn plikowy i seed administratora ----------------------------------


def test_file_store_persists_accounts(tmp_path: Path) -> None:
    path = tmp_path / "accounts.json"
    service1 = AccountService(FileAccountStore(path), allow_registration=True)
    service1.register("ala", "haslo-123")
    # Nowy magazyn z tego samego pliku widzi konto (trwałość).
    service2 = AccountService(FileAccountStore(path), allow_registration=True)
    account, _ = service2.login("ala", "haslo-123")
    assert account.username == "ala"


class _FakeSecrets:
    def __init__(self, value: str | None) -> None:
        self._value = value

    def resolve(self, ref: str) -> str | None:
        return self._value


def test_build_service_seeds_admin_from_secret() -> None:
    auth = AuthConfig(seed_admin_username="hetman", seed_admin_password_ref="env:X")
    service = build_account_service(auth, secrets=_FakeSecrets("silne-haslo-admina"))
    account, _ = service.login("hetman", "silne-haslo-admina")
    assert account.role == "admin"
    assert account.token_quota is None


def test_seed_admin_fails_closed_when_secret_missing() -> None:
    auth = AuthConfig(seed_admin_username="hetman", seed_admin_password_ref="env:X")
    with pytest.raises(AccountError):
        build_account_service(auth, secrets=_FakeSecrets(None))


def test_build_service_without_seed_is_empty() -> None:
    service = build_account_service(AuthConfig())
    assert service.list_accounts() == []
