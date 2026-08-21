"""Wyjątki routera modeli.

Hierarchia pozwala odróżnić błąd wyboru modelu (brak kandydata) od błędu
backendu (sieć/HTTP/parsowanie) oraz od wyczerpania limitów.
"""

from __future__ import annotations

# Baza hierarchii mieszka w `husarz.core.errors` — patrz tamtejszy docstring: dziedziczy
# z niej `EgressError`, zgłaszany przez warstwy NIŻSZE niż router (ssrf, narzędzia, wtyczki),
# a trzymanie jej tutaj tworzyło cykl importów. Re-eksport zachowuje publiczną nazwę.
from husarz.core.errors import RouterError

__all__ = [
    "AllModelsFailedError",
    "ModelBackendError",
    "NoModelAvailableError",
    "RateLimitExceededError",
    "RouterError",
    "TransportError",
]


class NoModelAvailableError(RouterError):
    """Dla żądania nie znaleziono żadnego pasującego, włączonego modelu."""


class TransportError(RouterError):
    """Błąd warstwy transportu (np. HTTP/sieć). Klient zamienia go na ModelBackendError."""


class ModelBackendError(RouterError):
    """Backend modelu zwrócił błąd lub nieprawidłową odpowiedź."""

    def __init__(self, model_id: str, message: str) -> None:
        self.model_id = model_id
        super().__init__(f"[{model_id}] {message}")


class AllModelsFailedError(RouterError):
    """Wszyscy kandydaci (model + fallbacki) zawiedli."""

    def __init__(self, failures: list[tuple[str, str]]) -> None:
        self.failures = failures
        detail = "; ".join(f"{mid}: {msg}" for mid, msg in failures) or "brak prób"
        super().__init__(f"Wszystkie modele zawiodły ({detail}).")


class RateLimitExceededError(RouterError):
    """Przekroczono limit żądań (kontrola kosztów)."""
