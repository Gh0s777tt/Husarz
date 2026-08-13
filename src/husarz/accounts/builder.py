"""Budowa ``AccountService`` z konfiguracji (magazyn + seed administratora).

Utrzymuje zasadę „zero hardcode": ścieżka magazynu i limity pochodzą z configu, a
hasło seed-admina z **referencji do sekretu** (nigdy z pliku konfiguracji). Seed
wykonywany jest tylko, gdy magazyn jest pusty — idempotentnie po restarcie.
"""

from __future__ import annotations

from husarz.accounts.errors import AccountError
from husarz.accounts.service import AccountService
from husarz.accounts.store import FileAccountStore, InMemoryAccountStore
from husarz.config.schema import AuthConfig
from husarz.config.secrets import NullSecretsProvider, SecretsProvider


def build_account_service(
    auth: AuthConfig,
    *,
    secrets: SecretsProvider | None = None,
) -> AccountService:
    """Buduje ``AccountService`` wg ``security.auth`` i (opcjonalnie) zasiewa admina."""
    store = (
        FileAccountStore(auth.accounts_path)
        if auth.accounts_path is not None
        else InMemoryAccountStore()
    )
    service = AccountService(
        store,
        session_ttl_seconds=auth.session_ttl_minutes * 60,
        allow_registration=auth.allow_registration,
        default_role=auth.default_user_role,
        default_token_quota=auth.default_token_quota,
    )
    _seed_admin(service, auth, secrets or NullSecretsProvider())
    return service


def _seed_admin(service: AccountService, auth: AuthConfig, secrets: SecretsProvider) -> None:
    """Tworzy konto administratora, gdy skonfigurowano seed, a magazyn jest pusty."""
    if not auth.seed_admin_username or not auth.seed_admin_password_ref:
        return
    if service.has_account(auth.seed_admin_username):
        return
    password = secrets.resolve(auth.seed_admin_password_ref)
    if not password or not password.strip():
        # Fail-closed: seed skonfigurowany, ale nierozwiązywalny → nie tworzymy konta
        # z pustym/nieznanym hasłem. Administrator naprawia referencję sekretu.
        raise AccountError(
            f"Seed admina: nie udało się rozwiązać hasła "
            f"(seed_admin_password_ref='{auth.seed_admin_password_ref}')."
        )
    service.create_account(
        auth.seed_admin_username.strip(), password.strip(), role="admin", token_quota=None
    )
