"""Model konta i magazyn kont (wstrzykiwalny: pamięć / plik JSON).

Magazyn jest abstrakcją (Protocol) — testy używają wersji w pamięci, wdrożenie
pliku JSON (konfinacja ścieżki), a produkcyjnie można podstawić Postgres bez zmian
w rdzeniu. Hasła trafiają tu WYŁĄCZNIE jako hash (patrz ``passwords``).
"""

from __future__ import annotations

import json
import secrets
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Protocol, runtime_checkable

from husarz.accounts.errors import AccountError


@dataclass(slots=True, frozen=True)
class Account:
    """Konto użytkownika. ``token_quota=None`` oznacza brak limitu (tryb suwerenny)."""

    user_id: str
    username: str
    password_hash: str
    role: str
    token_quota: int | None = None
    tokens_used: int = 0
    created_at: str = ""


@dataclass(slots=True, frozen=True)
class Principal:
    """Uwierzytelniony wywołujący — użytkownik z sesji albo statyczny token maszynowy."""

    role: str
    user_id: str | None = None
    username: str | None = None


def new_user_id() -> str:
    """Generuje nieprzewidywalny identyfikator konta."""
    return secrets.token_hex(16)


@runtime_checkable
class AccountStore(Protocol):
    """Interfejs magazynu kont."""

    def add(self, account: Account) -> None:
        """Zapisuje NOWE konto. Rzuca ``AccountError`` przy kolizji nazwy."""
        ...

    def get_by_username(self, username: str) -> Account | None:
        """Zwraca konto po nazwie użytkownika (case-insensitive) albo ``None``."""
        ...

    def get(self, user_id: str) -> Account | None:
        """Zwraca konto po identyfikatorze albo ``None``."""
        ...

    def update(self, account: Account) -> None:
        """Zapisuje zmieniony stan istniejącego konta."""
        ...

    def list_accounts(self) -> list[Account]:
        """Zwraca wszystkie konta (kopia)."""
        ...


def _norm(username: str) -> str:
    return username.strip().casefold()


class InMemoryAccountStore:
    """Magazyn kont w pamięci (domyślny; do testów i dev bez trwałości)."""

    def __init__(self) -> None:
        self._by_id: dict[str, Account] = {}
        self._id_by_name: dict[str, str] = {}

    def add(self, account: Account) -> None:  # noqa: D102 - patrz Protocol
        key = _norm(account.username)
        if key in self._id_by_name:
            raise AccountError(f"Użytkownik '{account.username}' już istnieje.")
        self._by_id[account.user_id] = account
        self._id_by_name[key] = account.user_id

    def get_by_username(self, username: str) -> Account | None:  # noqa: D102
        uid = self._id_by_name.get(_norm(username))
        return self._by_id.get(uid) if uid else None

    def get(self, user_id: str) -> Account | None:  # noqa: D102
        return self._by_id.get(user_id)

    def update(self, account: Account) -> None:  # noqa: D102
        if account.user_id not in self._by_id:
            raise AccountError(f"Konto '{account.user_id}' nie istnieje.")
        self._by_id[account.user_id] = account

    def list_accounts(self) -> list[Account]:  # noqa: D102
        return list(self._by_id.values())


class FileAccountStore(InMemoryAccountStore):
    """Magazyn kont w pliku JSON (konfinowana ścieżka). Odczyt przy starcie, zapis
    po każdej mutacji. Dla pojedynczego procesu; wielo-proces → backend współdzielony.
    """

    def __init__(self, path: str | Path) -> None:
        super().__init__()
        self._path = Path(path)
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:  # pragma: no cover - uszkodzony plik
            raise AccountError(f"Nie można wczytać kont z {self._path}: {exc}") from exc
        for item in data.get("accounts", []):
            account = Account(**item)
            self._by_id[account.user_id] = account
            self._id_by_name[_norm(account.username)] = account.user_id

    def _persist(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"accounts": [asdict(a) for a in self._by_id.values()]}
        self._path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def add(self, account: Account) -> None:  # noqa: D102
        super().add(account)
        self._persist()

    def update(self, account: Account) -> None:  # noqa: D102
        super().update(account)
        self._persist()


def with_usage(account: Account, tokens: int) -> Account:
    """Zwraca kopię konta z powiększonym licznikiem zużytych tokenów."""
    return replace(account, tokens_used=account.tokens_used + max(0, tokens))
