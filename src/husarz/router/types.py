"""Typy danych routera: żądanie i odpowiedź czatu (niezależne od backendu).

To struktury runtime (nie konfiguracja), dlatego dataclasses — lekkie i jawne.
Kształt odpowiada standardowi OpenAI-compat (chat/completions).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Dozwolone role wiadomości (standard OpenAI-compat).
ROLES = ("system", "user", "assistant", "tool")


@dataclass(slots=True, frozen=True)
class ImagePart:
    """Obraz dołączony do wiadomości (multimodal). ``data_b64`` to base64 BEZ prefiksu
    ``data:``; ``mime`` jest typem rozpoznanym z bajtów (nie zadeklarowanym przez klienta)."""

    mime: str
    data_b64: str


@dataclass(slots=True)
class ChatMessage:
    """Pojedyncza wiadomość w konwersacji (opcjonalnie z obrazami — modele wizyjne)."""

    role: str
    content: str
    images: list[ImagePart] = field(default_factory=list)

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
class UsageMeter:
    """Sumator zużycia tokenów dla JEDNEJ operacji (np. całej orkiestracji).

    Orkiestracja to wiele wywołań modelu (plan → N delegacji → refleksja → synteza), a limit
    tokenów konta rozliczany był wyłącznie z pojedynczej odpowiedzi czatu — czyli najdroższy
    endpoint nie naliczał niczego. Ten sumator zbiera zużycie ze wszystkich wywołań.

    Instancja jest ŚWIEŻA na każde żądanie (nigdy współdzielona między wątkami): orkiestrator
    tworzy ją w ``run`` i przekazuje w dół, tak jak ``ToolCallBudget``.
    """

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    # Czy KTÓRYKOLWIEK backend zgłosił zużycie. Bez tego nie odróżnilibyśmy „zero tokenów"
    # od „backend nie raportuje" — a to różnica między naliczeniem 0 a brakiem danych.
    reported: bool = False

    def add(self, usage: Usage | None) -> None:
        """Dolicza zużycie z jednej odpowiedzi modelu (``None`` i pola ``None`` pomijane)."""
        if usage is None:
            return
        for field_name in ("prompt_tokens", "completion_tokens", "total_tokens"):
            value = getattr(usage, field_name)
            if value is not None:
                setattr(self, field_name, getattr(self, field_name) + int(value))
                self.reported = True

    def snapshot(self) -> Usage | None:
        """Zwraca sumę jako ``Usage`` albo ``None``, gdy żaden backend nic nie zgłosił."""
        if not self.reported:
            return None
        return Usage(
            prompt_tokens=self.prompt_tokens,
            completion_tokens=self.completion_tokens,
            total_tokens=self.total_tokens,
        )


@dataclass(slots=True)
class ChatResponse:
    """Odpowiedź modelu wraz z metadanymi."""

    model: str  # id modelu (z rejestru), który udzielił odpowiedzi
    content: str
    finish_reason: str | None = None
    usage: Usage | None = None
    raw: dict[str, Any] = field(default_factory=dict)
