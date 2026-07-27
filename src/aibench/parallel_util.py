"""Small helpers for parallel map with ordered results."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import TypeVar

T = TypeVar("T")
R = TypeVar("R")


def parallel_map(
    fn: Callable[[T], R],
    items: Iterable[T],
    *,
    workers: int = 1,
) -> list[R]:
    seq = list(items)
    if workers <= 1 or len(seq) <= 1:
        return [fn(x) for x in seq]
    results: list[R | None] = [None] * len(seq)
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(fn, item): i for i, item in enumerate(seq)}
        for fut in as_completed(futs):
            i = futs[fut]
            results[i] = fut.result()
    return [r for r in results if r is not None]  # type: ignore[misc]
