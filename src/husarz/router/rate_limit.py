"""Prosty ogranicznik żądań (token bucket) — kontrola kosztów.

Zegar jest wstrzykiwalny (``now_fn``), więc test jest w pełni deterministyczny.
Domyślnie używamy ``time.monotonic`` (odporny na zmiany zegara systemowego).
"""

from __future__ import annotations

import time
from collections.abc import Callable

from husarz.router.errors import RateLimitExceededError


class RateLimiter:
    """Token bucket o pojemności ``max_per_minute`` i uzupełnianiu w tempie/min."""

    def __init__(
        self,
        max_per_minute: int,
        *,
        now_fn: Callable[[], float] = time.monotonic,
    ) -> None:
        if max_per_minute <= 0:
            raise ValueError("max_per_minute musi być dodatnie.")
        self._capacity = float(max_per_minute)
        self._tokens = float(max_per_minute)
        self._refill_per_second = max_per_minute / 60.0
        self._now = now_fn
        self._last = now_fn()

    def acquire(self) -> None:
        """Pobiera jeden token lub rzuca ``RateLimitExceededError``."""
        now = self._now()
        elapsed = max(0.0, now - self._last)
        self._last = now
        self._tokens = min(self._capacity, self._tokens + elapsed * self._refill_per_second)
        if self._tokens < 1.0:
            raise RateLimitExceededError(
                f"Przekroczono limit {int(self._capacity)} żądań/min (kontrola kosztów)."
            )
        self._tokens -= 1.0
