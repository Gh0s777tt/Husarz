"""Wspólne typy warstwy narzędzi.

Każde narzędzie ma atrybut ``name`` (klucz w rejestrze) i zwraca ``ToolResult``.
Konkretne narzędzia mają własne, typowane metody (np. ``read``/``write``,
``run``, ``fetch``) — jednolita pętla function-calling wpięta zostanie do agenta
Towarzysz w dalszej iteracji.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass(slots=True)
class ToolResult:
    """Wynik wywołania narzędzia — jednolity kontrakt zwrotny."""

    tool: str
    ok: bool
    output: str = ""
    error: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class Tool(Protocol):
    """Minimalny protokół narzędzia: identyfikacja w rejestrze."""

    name: str
