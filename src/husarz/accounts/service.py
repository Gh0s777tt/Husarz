"""AccountService — rejestracja, logowanie (sesje), limity i rozliczanie tokenów.

Spina magazyn kont, hashowanie haseł i sesje w jedną, testowalną usługę. Zegar i
magazyn są wstrzykiwalne (testy deterministyczne, bez sieci/DB). Sesje trzymane w
pamięci procesu (MVP) — po restarcie użytkownicy logują się ponownie.
"""

from __future__ import annotations

import secrets
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from husarz.accounts.errors import (
    AccountLockedError,
    AuthenticationError,
    QuotaExceededError,
    RegistrationDisabledError,
)
from husarz.accounts.passwords import hash_password, verify_password
from husarz.accounts.store import (
    Account,
    AccountStore,
    InMemoryAccountStore,
    new_user_id,
    with_usage,
)


def _default_clock() -> datetime:
    return datetime.now(UTC)


@dataclass(slots=True)
class _SessionData:
    user_id: str
    expires_at: float  # epoch (UTC)


class AccountService:
    """Logika kont: rejestracja, logowanie, sesje, limity i zużycie tokenów."""

    def __init__(
        self,
        store: AccountStore | None = None,
        *,
        clock: Callable[[], datetime] = _default_clock,
        session_ttl_seconds: int = 12 * 3600,
        allow_registration: bool = False,
        default_role: str = "user",
        default_token_quota: int | None = None,
        login_max_attempts: int = 5,
        login_lockout_seconds: int = 15 * 60,
        max_sessions_per_user: int = 20,
    ) -> None:
        self._store: AccountStore = store if store is not None else InMemoryAccountStore()
        self._clock = clock
        self._ttl = session_ttl_seconds
        self._allow_registration = allow_registration
        self._default_role = default_role
        self._default_quota = default_token_quota
        self._max_attempts = login_max_attempts
        self._lockout = login_lockout_seconds
        self._max_sessions = max_sessions_per_user
        self._sessions: dict[str, _SessionData] = {}
        # Znaczniki czasu nieudanych logowań per (znormalizowana) nazwa — anty-brute-force.
        self._failed: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    # --- Tworzenie kont ----------------------------------------------------

    def create_account(
        self,
        username: str,
        password: str,
        *,
        role: str | None = None,
        token_quota: int | None = None,
    ) -> Account:
        """Tworzy konto (ścieżka administracyjna/seed — bez bramki rejestracji)."""
        username = username.strip()
        if not username:
            raise AuthenticationError("Nazwa użytkownika nie może być pusta.")
        account = Account(
            user_id=new_user_id(),
            username=username,
            password_hash=hash_password(password),
            role=role if role is not None else self._default_role,
            token_quota=token_quota if token_quota is not None else self._default_quota,
            tokens_used=0,
            created_at=self._clock().isoformat(),
        )
        self._store.add(account)  # AccountError przy kolizji nazwy
        return account

    def register(self, username: str, password: str) -> Account:
        """Rejestracja użytkownika — tylko gdy ``allow_registration`` jest włączone."""
        if not self._allow_registration:
            raise RegistrationDisabledError(
                "Rejestracja jest wyłączona. Konto może utworzyć administrator."
            )
        return self.create_account(username, password)

    # --- Logowanie i sesje -------------------------------------------------

    def _prune_failures(self, key: str, now: float) -> list[float]:
        """Zwraca (i zapisuje) nieudane próby z bieżącego okna blokady dla klucza."""
        recent = [t for t in self._failed.get(key, []) if t >= now - self._lockout]
        if recent:
            self._failed[key] = recent
        else:
            self._failed.pop(key, None)
        return recent

    def authenticate(self, username: str, password: str) -> Account:
        """Weryfikuje poświadczenia. Rzuca ``AuthenticationError``/``AccountLockedError``."""
        key = username.strip().casefold()
        now = self._clock().timestamp()
        with self._lock:
            if len(self._prune_failures(key, now)) >= self._max_attempts:
                raise AccountLockedError(
                    "Konto tymczasowo zablokowane po zbyt wielu nieudanych próbach. "
                    "Spróbuj ponownie później."
                )
        account = self._store.get_by_username(username)
        # Weryfikujemy hash nawet przy braku konta — zbliżony czas odpowiedzi
        # (ogranicza enumerację użytkowników przez pomiar czasu).
        reference = account.password_hash if account else _DUMMY_HASH
        ok = verify_password(password, reference)
        if account is None or not ok:
            with self._lock:
                self._failed.setdefault(key, []).append(now)
            raise AuthenticationError("Nieprawidłowa nazwa użytkownika lub hasło.")
        with self._lock:
            self._failed.pop(key, None)  # udane logowanie czyści licznik blokady
        return account

    def login(self, username: str, password: str) -> tuple[Account, str]:
        """Loguje użytkownika i zwraca ``(konto, token_sesji)``."""
        account = self.authenticate(username, password)
        token = secrets.token_urlsafe(32)
        now = self._clock().timestamp()
        with self._lock:
            self._sweep_sessions(now)
            self._cap_user_sessions(account.user_id)
            self._sessions[token] = _SessionData(account.user_id, now + self._ttl)
        return account, token

    def _sweep_sessions(self, now: float) -> None:
        """Usuwa wygasłe sesje (ogranicza narastanie pamięci). Wołane pod zamkiem."""
        expired = [tok for tok, data in self._sessions.items() if now >= data.expires_at]
        for tok in expired:
            del self._sessions[tok]

    def _cap_user_sessions(self, user_id: str) -> None:
        """Ogranicza liczbę aktywnych sesji na użytkownika (usuwa najstarsze). Pod zamkiem."""
        owned = [(tok, d.expires_at) for tok, d in self._sessions.items() if d.user_id == user_id]
        if len(owned) < self._max_sessions:
            return
        owned.sort(key=lambda item: item[1])  # najstarsze (najwcześniej wygasające) pierwsze
        for tok, _ in owned[: len(owned) - self._max_sessions + 1]:
            del self._sessions[tok]

    def resolve_session(self, token: str) -> Account | None:
        """Zwraca konto dla ważnego tokenu sesji albo ``None`` (wygasł/nieznany)."""
        with self._lock:
            data = self._sessions.get(token)
            if data is None:
                return None
            if self._clock().timestamp() >= data.expires_at:
                del self._sessions[token]
                return None
            user_id = data.user_id
        return self._store.get(user_id)

    def logout(self, token: str) -> None:
        """Unieważnia sesję (idempotentnie)."""
        with self._lock:
            self._sessions.pop(token, None)

    # --- Limity i zużycie tokenów -----------------------------------------

    def check_quota(self, user_id: str) -> None:
        """Rzuca ``QuotaExceededError``, gdy konto wyczerpało limit tokenów.

        Limit jest z natury MIĘKKI: rozliczenie tokenów następuje po odpowiedzi
        modelu, więc równoległe żądania tuż przy progu mogą go nieznacznie przekroczyć.
        Sprawdzenie pod tym samym zamkiem co ``record_usage`` (spójny odczyt).
        """
        with self._lock:
            account = self._store.get(user_id)
            if account is None or account.token_quota is None:
                return
            if account.tokens_used >= account.token_quota:
                raise QuotaExceededError("Wyczerpano limit tokenów konta.")

    def record_usage(self, user_id: str, tokens: int) -> None:
        """Dolicza zużyte tokeny do konta (atomowo)."""
        if tokens <= 0:
            return
        with self._lock:
            account = self._store.get(user_id)
            if account is not None:
                self._store.update(with_usage(account, tokens))

    def get(self, user_id: str) -> Account | None:
        """Zwraca konto po identyfikatorze."""
        return self._store.get(user_id)

    def has_account(self, username: str) -> bool:
        """Czy istnieje konto o podanej nazwie użytkownika."""
        return self._store.get_by_username(username) is not None

    def list_accounts(self) -> list[Account]:
        """Zwraca wszystkie konta (kopia)."""
        return self._store.list_accounts()

    @property
    def registration_enabled(self) -> bool:
        """Czy rejestracja przez użytkowników jest włączona."""
        return self._allow_registration


# Stały, poprawny hash „na pusto" — do porównań przy braku konta (patrz authenticate).
_DUMMY_HASH = hash_password("husarz-dummy-reference")
