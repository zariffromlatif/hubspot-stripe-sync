"""Retry with exponential backoff + jitter for transient API failures."""
from __future__ import annotations

import random
import time
from typing import Callable, TypeVar

T = TypeVar("T")


class RetryableError(Exception):
    """Transient failure (HTTP 429/5xx, network). Safe to retry."""

    def __init__(self, message: str, retry_after: float | None = None):
        super().__init__(message)
        self.retry_after = retry_after


class PermanentError(Exception):
    """Non-retryable failure (bad request, auth, validation)."""


def with_retry(
    fn: Callable[[], T],
    *,
    attempts: int = 5,
    base_delay: float = 0.5,
    max_delay: float = 30.0,
    sleep: Callable[[float], None] = time.sleep,
    on_retry: Callable[[int, Exception, float], None] | None = None,
) -> T:
    """Call fn, retrying RetryableError up to `attempts` times total.

    Honours the server's Retry-After when given. PermanentError and any other
    exception propagate immediately.
    """
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except RetryableError as exc:
            if attempt == attempts:
                raise
            delay = exc.retry_after
            if delay is None:
                delay = min(max_delay, base_delay * 2 ** (attempt - 1))
                delay *= 0.5 + random.random() / 2  # jitter
            if on_retry:
                on_retry(attempt, exc, delay)
            sleep(delay)
    raise AssertionError("unreachable")
