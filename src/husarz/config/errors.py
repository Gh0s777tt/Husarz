"""Wyjątki i formatowanie błędów konfiguracji.

Cel: błąd konfiguracji ma być *czytelnym komunikatem*, a nie crashem ze
stack trace. Loader łapie ``pydantic.ValidationError`` i zamienia go na
``ConfigValidationError`` z listą lokalizacji i przyczyn.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import ValidationError


class ConfigError(Exception):
    """Bazowy wyjątek konfiguracji Husarza."""


class ConfigFileNotFoundError(ConfigError):
    """Brak wymaganego pliku konfiguracji."""


class ConfigParseError(ConfigError):
    """Błąd składni pliku (np. niepoprawny YAML)."""


class ConfigValidationError(ConfigError):
    """Konfiguracja nie przeszła walidacji schematem."""


def format_validation_error(exc: ValidationError, *, source: str | Path | None = None) -> str:
    """Zamienia ``ValidationError`` na czytelny, wielolinijkowy komunikat po polsku."""
    header = "Konfiguracja jest niepoprawna"
    if source is not None:
        header += f" (źródło: {source})"
    lines = [header + ":"]
    for err in exc.errors():
        loc = ".".join(str(part) for part in err.get("loc", ())) or "<root>"
        msg = err.get("msg", "nieznany błąd")
        lines.append(f"  - {loc}: {msg}")
    return "\n".join(lines)
