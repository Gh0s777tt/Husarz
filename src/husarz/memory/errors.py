"""Wyjątki pamięci długoterminowej (RAG)."""

from __future__ import annotations


class MemoryError_(Exception):
    """Bazowy wyjątek pamięci/RAG."""


class EmbedderError(MemoryError_):
    """Błąd embeddera (transport, wymiar wektora, nierozwiązywalny klucz)."""


class RagBackendError(MemoryError_):
    """Błąd budowy/konfiguracji backendu pamięci."""
