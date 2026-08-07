"""Generic retry with exponential backoff for flaky I/O (HTTP, DB)."""

from __future__ import annotations

import os
import random
import time
from collections.abc import Callable
from typing import TypeVar

T = TypeVar("T")

# Exceptions / status signals that are worth retrying
RETRYABLE_SUBSTRINGS = (
    "timeout",
    "timed out",
    "connection",
    "connect",
    "temporarily",
    "unavailable",
    "rate limit",
    "429",
    "502",
    "503",
    "504",
    "empty content",
    "reset by peer",
    "broken pipe",
    "server disconnected",
)


def retry_config() -> tuple[int, float, float]:
    """Return (max_attempts, base_backoff_s, max_backoff_s)."""
    max_attempts = max(1, int(os.environ.get("AIBENCH_RETRY_MAX", "3")))
    base = float(os.environ.get("AIBENCH_RETRY_BACKOFF", "1.0"))
    cap = float(os.environ.get("AIBENCH_RETRY_BACKOFF_MAX", "20.0"))
    return max_attempts, base, cap


#: Checked before the retryable list, because these arrive wearing a retryable status code.
#: A gateway reports an exhausted budget as HTTP 429 — the same code it uses for "you are
#: going too fast" — so a substring match on "429" alone retried a permanent failure five
#: times with backoff, per draft. Measured: 489 such retries in one run, 0 cases produced, and
#: the failures looked transient enough that --resume would have tried them all again.
NON_RETRYABLE_SUBSTRINGS = (
    "budget has been exceeded",
    "budget_exceeded",
    "insufficient_quota",
    "exceeded your current quota",
    "invalid_api_key",
    "authentication",
)


def is_retryable_error(exc: BaseException) -> bool:
    name = type(exc).__name__.lower()
    msg = str(exc).lower()
    if any(s in msg for s in NON_RETRYABLE_SUBSTRINGS):
        return False
    if "timeout" in name or "connection" in name:
        return True
    # httpx
    if name in {"readtimeout", "connecttimeout", "connecterror", "remoteprotocolerror"}:
        return True
    return any(s in msg for s in RETRYABLE_SUBSTRINGS)


def retry_call(
    fn: Callable[[], T],
    *,
    max_attempts: int | None = None,
    base_backoff_s: float | None = None,
    max_backoff_s: float | None = None,
    retry_if: Callable[[BaseException], bool] | None = None,
    on_retry: Callable[[int, BaseException, float], None] | None = None,
    label: str = "op",
) -> T:
    """Call fn() with retries on retryable errors.

    max_attempts includes the first try (e.g. 3 => up to 2 retries).
    """
    cfg_max, cfg_base, cfg_cap = retry_config()
    attempts = max_attempts if max_attempts is not None else cfg_max
    base = base_backoff_s if base_backoff_s is not None else cfg_base
    cap = max_backoff_s if max_backoff_s is not None else cfg_cap
    pred = retry_if or is_retryable_error

    last: BaseException | None = None
    for i in range(1, attempts + 1):
        try:
            return fn()
        except BaseException as e:
            last = e
            if i >= attempts or not pred(e):
                raise
            # exponential backoff + jitter
            delay = min(cap, base * (2 ** (i - 1)))
            delay = delay * (0.5 + random.random())  # 0.5x–1.5x
            if on_retry:
                on_retry(i, e, delay)
            else:
                print(f"[retry] {label} attempt {i}/{attempts} failed: {e}; sleep {delay:.2f}s")
            time.sleep(delay)
    assert last is not None
    raise last
