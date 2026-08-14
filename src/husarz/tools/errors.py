"""Wyjątki warstwy narzędzi."""

from __future__ import annotations


class ToolError(Exception):
    """Bazowy wyjątek narzędzi."""


class ToolNotAllowedError(ToolError):
    """Narzędzie nie jest dozwolone (wyłączone lub spoza allowlisty agenta)."""


class PathNotAllowedError(ToolError):
    """Ścieżka poza workspace lub zablokowana przez deny-glob."""


class CommandNotAllowedError(ToolError):
    """Polecenie/podkomenda spoza allowlisty."""


class EgressNotAllowedError(ToolError):
    """Ruch wychodzący zablokowany przez politykę egress / allowlistę domen."""


class SandboxError(ToolError):
    """Błąd sandboxa (brak obrazu, niedostępny silnik, timeout konfiguracji)."""


class FetchError(ToolError):
    """Awaria transportu HTTP narzędzia ``web`` (DNS, TCP, TLS, timeout, limit rozmiaru).

    Odpowiednik ``PluginTransportError`` po stronie wtyczek: pozwala narzędziu ``web``
    zdegradować awarię sieci do ``ToolResult(ok=False)`` zamiast przepuścić surowy wyjątek
    ``httpx`` przez pętlę agenta. Komunikat jest GENERYCZNY — nie echuje URL ani wnętrzności
    biblioteki (nie wyciekają do audytu/API).
    """
