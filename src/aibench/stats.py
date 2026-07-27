"""Statistical helpers for benchmark summaries."""

from __future__ import annotations

import math
from collections import defaultdict
from typing import Any


def wilson_ci(successes: int, n: int, z: float = 1.96) -> tuple[float, float] | None:
    """Wilson score interval for binomial proportion; returns (lo, hi) or None if n=0."""
    if n <= 0:
        return None
    phat = successes / n
    z2 = z * z
    denom = 1.0 + z2 / n
    center = (phat + z2 / (2 * n)) / denom
    margin = (z / denom) * math.sqrt(phat * (1 - phat) / n + z2 / (4 * n * n))
    lo = max(0.0, center - margin)
    hi = min(1.0, center + margin)
    return (lo, hi)


def format_wilson_ci(successes: int, n: int, z: float = 1.96) -> str | None:
    ci = wilson_ci(successes, n, z=z)
    if ci is None:
        return None
    return f"[{ci[0] * 100:.1f}%, {ci[1] * 100:.1f}%]"


def stratify_results(
    case_results: list[dict[str, Any]],
    *,
    key: str,
) -> dict[str, dict[str, Any]]:
    """Group case rows by metadata field; compute per-stratum success rate + Wilson CI."""
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in case_results:
        if r.get("infra_error"):
            continue
        label = r.get(key) or (r.get("metadata") or {}).get(key) or "unknown"
        buckets[str(label)].append(r)

    out: dict[str, dict[str, Any]] = {}
    for label, rows in sorted(buckets.items()):
        n = len(rows)
        s = sum(1 for r in rows if r.get("passed"))
        rate = (s / n) if n else 0.0
        out[label] = {
            "n": n,
            "successes": s,
            "success_rate": rate,
            "confidence_interval": format_wilson_ci(s, n),
        }
    return out
