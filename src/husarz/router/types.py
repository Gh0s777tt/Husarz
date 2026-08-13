"""Typy danych routera: żądanie i odpowiedź czatu (niezależne od backendu).

To struktury runtime (nie konfiguracja), dlatego dataclasses — lekkie i jawne.
Kształt odpowiada standardowi OpenAI-compat (chat/completions).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Dozwolone role wiadomości (standard OpenAI-compat).
ROLES = ("system", "user", "assistant", "tool")


@dataclass(slots=True)
class ChatMessage:
    """Pojedyncza wiadomość w konwersacji."""

    role: str
    content: str

    def __post_init__(self) -> None:
        if self.role not in ROLES:
            raise ValueError(
                f"Nieprawidłowa rola wiadomości: {self.role!r}. Dozwolone: {', '.join(ROLES)}."
            )


@dataclass(slots=True)
class ChatRequest:
    """Żądanie uzupełnienia czatu, niezależne od modelu/backendu."""

    messages: list[ChatMessage]
    max_tokens: int | None = None
    temperature: float | None = None
    stop: list[str] | None = None
    # Dodatkowe parametry przekazywane wprost do backendu (np. top_p).
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class Usage:
    """Zużycie tokenów zgłoszone przez backend (jeśli dostępne)."""

    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None


@dataclass(slots=True)
class ChatResponse:
    """Odpowiedź modelu wraz z metadanymi."""

    model: str  # id modelu (z rejestru), który udzielił odpowiedzi
    content: str
    finish_reason: str | None = None
    usage: Usage | None = None
    raw: dict[str, Any] = field(default_factory=dict)
