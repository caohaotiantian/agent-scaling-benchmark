"""Small helpers for parallel map with ordered results."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import TypeVar, cast

T = TypeVar("T")
R = TypeVar("R")


def parallel_map(
    fn: Callable[[T], R],
    items: Iterable[T],
    *,
    workers: int = 1,
) -> list[R]:
    """Map ``fn`` over ``items``, preserving input order.

    Both branches return one result per input, ``None`` results included. The parallel branch
    used to drop them, so a function that legitimately returns ``None`` produced a different
    list at ``workers=1`` than at ``workers=4`` — the wrong primitive for a harness whose whole
    claim is "swap the agent, rerun, verify every reported number".
    """
    seq = list(items)
    if workers <= 1 or len(seq) <= 1:
        return [fn(x) for x in seq]
    # Every slot is filled before the return: a worker that raises propagates out of the
    # `with` block rather than leaving a hole behind.
    results: list[R | None] = [None] * len(seq)
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(fn, item): i for i, item in enumerate(seq)}
        for fut in as_completed(futs):
            results[futs[fut]] = fut.result()
    return cast("list[R]", results)
