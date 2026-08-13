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
