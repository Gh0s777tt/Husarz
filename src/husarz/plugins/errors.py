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


class PluginCallDeniedError(PluginError):
    """Wywołanie ``tools/call`` odmówione lokalnie PRZED wyjściem na sieć.

    Powód: ``allow_call=false`` (master-switch) albo nazwa zdalnego narzędzia spoza
    ``call_allowlist`` wtyczki. Deny-by-default — odkrywanie (``list``) jest osobne i
    bramkowane tylko przez ``enabled``. Fail-closed: transport NIE jest dotykany.
    """


class PluginArgsError(PluginError):
    """Argumenty ``tools/call`` odrzucone lokalnie: zły typ lub przekroczony ``max_call_bytes``.

    Cap żądania (rozmiar zserializowanych ``params``) jest egzekwowany PRZED egress —
    ogranicza powierzchnię eksfiltracji przez model i chroni serwer/przewód (DoS).
    """
