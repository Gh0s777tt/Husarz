"""Testy ogranicznika żądań (token bucket) z wstrzykniętym zegarem."""

from __future__ import annotations

import pytest

from husarz.router import RateLimiter, RateLimitExceededError

pytestmark = pytest.mark.unit


class Clock:
    """Deterministyczny zegar testowy."""

    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


def test_allows_up_to_capacity_then_blocks() -> None:
    clock = Clock()
    limiter = RateLimiter(2, now_fn=clock)
    limiter.acquire()
    limiter.acquire()
    with pytest.raises(RateLimitExceededError):
        limiter.acquire()


def test_refills_over_time() -> None:
    clock = Clock()
    limiter = RateLimiter(2, now_fn=clock)
    limiter.acquire()
    limiter.acquire()
    clock.advance(30)  # przy 2/min to +1 token
    limiter.acquire()  # nie rzuca
    with pytest.raises(RateLimitExceededError):
        limiter.acquire()


def test_rejects_nonpositive_capacity() -> None:
    with pytest.raises(ValueError, match="dodatnie"):
        RateLimiter(0)
