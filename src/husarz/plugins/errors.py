"""Wyjątki systemu wtyczek (konektory MCP)."""

from __future__ import annotations


class PluginError(Exception):
    """Bazowy wyjątek wtyczek/konektorów."""


class PluginNotFoundError(PluginError):
    """Nieznana wtyczka (brak w konfiguracji)."""


class PluginDisabledError(PluginError):
    """Wtyczka istnieje, ale jest wyłączona (``enabled: false``)."""


class PluginAuthError(PluginError):
    """Zdalna odmowa autoryzacji od serwera wtyczki (401/403)."""


class PluginSecretError(PluginError):
    """Lokalna błędna konfiguracja: ``token_ref`` ustawiony, ale sekret nierozwiązywalny.

    Oddzielony od ``PluginAuthError`` (zdalna odmowa), by API nie przypisywało lokalnej
    literówki serwerowi wtyczki. Fail-closed — podnoszony PRZED wyjściem na sieć.
    """


class PluginTransportError(PluginError):
    """Błąd warstwy transportu HTTP (sieć/limit rozmiaru) — komunikat generyczny."""
