"""Dostawcy sekretów.

Sekrety NIGDY nie są trzymane w repo ani w plikach konfiguracji. W konfiguracji
mogą występować jedynie *referencje* (np. ``vault:secret/data/husarz#token``),
które ten moduł rozwiązuje do wartości w czasie działania.

Etap 0 dostarcza interfejs i dwie bezpieczne implementacje:
  - ``NullSecretsProvider`` — nic nie zwraca (domyślna, brak zależności),
  - ``EnvSecretsProvider`` — rozwiązuje referencje ze zmiennych środowiskowych.

Dostawcy Vault i SOPS/age zostaną dołączeni w Etapie 4 (bezpieczeństwo) —
tutaj rejestrujemy dla nich miejsce, by nie łamać zasady "zero hardcode".
"""

from __future__ import annotations

import os
from typing import Protocol, runtime_checkable

from husarz.config.schema import SecretsProviderKind


@runtime_checkable
class SecretsProvider(Protocol):
    """Interfejs dostawcy sekretów."""

    def resolve(self, ref: str) -> str | None:
        """Zwraca wartość sekretu dla referencji albo ``None``, gdy brak."""
        ...


class NullSecretsProvider:
    """Dostawca, który nic nie zwraca. Bezpieczny domyślny wybór."""

    def resolve(self, ref: str) -> str | None:  # noqa: D102 - patrz Protocol
        return None


class EnvSecretsProvider:
    """Rozwiązuje referencje ``env:NAZWA`` (lub samą NAZWĘ) ze środowiska."""

    def resolve(self, ref: str) -> str | None:  # noqa: D102 - patrz Protocol
        name = ref.split(":", 1)[1] if ref.startswith("env:") else ref
        return os.environ.get(name)


def get_secrets_provider(kind: SecretsProviderKind) -> SecretsProvider:
    """Fabryka dostawcy sekretów na podstawie rodzaju.

    Vault i SOPS zwracają na razie ``NotImplementedError`` z jasnym komunikatem —
    będą zaimplementowane w Etapie 4. Dzięki temu konfiguracja może już się do
    nich odwoływać, a start w profilu, który ich nie używa, nie jest blokowany.
    """
    if kind is SecretsProviderKind.NONE:
        return NullSecretsProvider()
    if kind is SecretsProviderKind.ENV:
        return EnvSecretsProvider()
    if kind in (SecretsProviderKind.VAULT, SecretsProviderKind.SOPS):
        raise NotImplementedError(
            f"Dostawca sekretów '{kind.value}' będzie dostępny w Etapie 4 "
            f"(bezpieczeństwo). Użyj 'none' lub 'env' w Etapie 0."
        )
    raise ValueError(f"Nieznany rodzaj dostawcy sekretów: {kind!r}")
