"""Wyjątki integracji Git (GitHub/GitLab)."""

from __future__ import annotations


class GitError(Exception):
    """Bazowy wyjątek integracji Git."""


class GitAuthError(GitError):
    """Nieprawidłowy/niedostępny token dostawcy (401/403) lub brak sekretu."""


class GitTransportError(GitError):
    """Błąd warstwy transportu HTTP (sieć)."""


class GitConnectionError(GitError):
    """Nieznane połączenie lub kolizja nazwy połączenia."""
