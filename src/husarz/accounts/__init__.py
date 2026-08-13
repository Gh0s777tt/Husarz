"""Konta użytkowników: rejestracja, logowanie (sesje), limity i zużycie tokenów.

Warstwa suwerenna i testowalna: hashowanie ``scrypt`` (bez zależności), magazyn
wstrzykiwalny (pamięć/plik/Postgres), sesje w pamięci procesu. Spina się z
uwierzytelnianiem API (token sesji jako Bearer) i RBAC (``husarz.security.rbac``).
"""

from __future__ import annotations

from husarz.accounts.errors import (
    AccountError,
    AccountLockedError,
    AuthenticationError,
    QuotaExceededError,
    RegistrationDisabledError,
)
from husarz.accounts.passwords import hash_password, verify_password
from husarz.accounts.service import AccountService
from husarz.accounts.store import (
    Account,
    AccountStore,
    FileAccountStore,
    InMemoryAccountStore,
    Principal,
    new_user_id,
)

__all__ = [
    "Account",
    "AccountError",
    "AccountLockedError",
    "AccountService",
    "AccountStore",
    "AuthenticationError",
    "FileAccountStore",
    "InMemoryAccountStore",
    "Principal",
    "QuotaExceededError",
    "RegistrationDisabledError",
    "hash_password",
    "new_user_id",
    "verify_password",
]
