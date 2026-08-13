"""Wyjątki warstwy kont."""

from __future__ import annotations


class AccountError(Exception):
    """Bazowy wyjątek operacji na kontach."""


class RegistrationDisabledError(AccountError):
    """Rejestracja jest wyłączona (tryb „dla wybranych"; konta tworzy admin)."""


class AuthenticationError(AccountError):
    """Nieprawidłowe poświadczenia (zła nazwa lub hasło)."""


class QuotaExceededError(AccountError):
    """Wyczerpany limit tokenów konta."""
