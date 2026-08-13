"""VectorStore — magazyn wektorów z wyszukiwaniem po cosinusie.

``InMemoryVectorStore`` (czysty Python, bez numpy) to domyślny magazyn MVP: najlżejszy,
w pełni offline-testowalny, bez powierzchni at-rest (tylko RAM). Trwały magazyn (SQLite)
i szyfrowanie at-rest wchodzą w Etapie 14b (razem z przewleczeniem sekretów).

Izolacja: każdy rekord należy do ``namespace`` (kolekcji); ``search`` skanuje WYŁĄCZNIE
podany namespace — zatruty ``add`` jednej kolekcji nie wypływa w ``search`` innej. Wzrost
jest ograniczony (``max_items`` + ewikcja FIFO) — model-sterowany feed nie rośnie w nieskończoność.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable


@dataclass(slots=True, frozen=True)
class Hit:
    """Trafienie wyszukiwania: identyfikator, wynik (cosine) i ładunek rekordu."""

    item_id: str
    score: float
    payload: dict[str, Any]


@runtime_checkable
class VectorStore(Protocol):
    """Magazyn wektorów: zapis (z dedup po ``item_id``) i wyszukiwanie w namespace."""

    def upsert(
        self, namespace: str, item_id: str, vector: list[float], payload: dict[str, Any]
    ) -> None: ...

    def search(self, namespace: str, vector: list[float], top_k: int) -> list[Hit]: ...

    def count(self, namespace: str) -> int: ...


def cosine(a: list[float], b: list[float]) -> float:
    """Podobieństwo cosinusowe dwóch wektorów (0.0, gdy któryś zerowy lub różnej długości)."""
    if len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    if na == 0.0 or nb == 0.0:
        return 0.0
    return float(dot / (na * nb))


class InMemoryVectorStore:
    """Magazyn w pamięci: cosine brute-force, izolacja namespace, cap + ewikcja FIFO."""

    def __init__(self, *, max_items: int = 5000) -> None:
        if max_items < 1:
            raise ValueError("max_items musi być >= 1.")
        self._max_items = max_items
        # namespace -> (item_id -> (vector, payload)); OrderedDict trzyma kolejność wstawień (FIFO).
        self._data: dict[str, OrderedDict[str, tuple[list[float], dict[str, Any]]]] = {}

    def upsert(
        self, namespace: str, item_id: str, vector: list[float], payload: dict[str, Any]
    ) -> None:
        bucket = self._data.setdefault(namespace, OrderedDict())
        if item_id in bucket:
            bucket.pop(item_id)  # aktualizacja → przenieś na koniec (świeży)
        bucket[item_id] = (list(vector), dict(payload))
        # Ewikcja FIFO: najstarsze rekordy usuwane po przekroczeniu limitu (anty-OOM).
        while len(bucket) > self._max_items:
            bucket.popitem(last=False)

    def search(self, namespace: str, vector: list[float], top_k: int) -> list[Hit]:
        bucket = self._data.get(namespace)
        if not bucket:
            return []
        scored = [
            Hit(item_id=item_id, score=cosine(vector, stored_vec), payload=payload)
            for item_id, (stored_vec, payload) in bucket.items()
        ]
        scored.sort(key=lambda hit: hit.score, reverse=True)
        return scored[: max(0, top_k)]

    def count(self, namespace: str) -> int:
        return len(self._data.get(namespace, ()))
