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


def mcnemar_test(b: int, c: int) -> dict[str, Any]:
    """Exact two-sided McNemar test on paired pass/fail outcomes.

    ``b`` counts cases only A solved, ``c`` cases only B solved. Cases both or neither solved
    carry no information about which is better and are excluded by construction — that is the
    whole point of pairing, and it is why this detects a difference two overlapping Wilson
    intervals would call inconclusive.
    """
    n = b + c
    if n == 0:
        return {"b": b, "c": c, "discordant": 0, "p_value": 1.0, "significant": False}
    k = min(b, c)
    tail = sum(math.comb(n, i) for i in range(k + 1)) * (0.5**n)
    p = min(1.0, 2.0 * tail)
    return {
        "b": b,
        "c": c,
        "discordant": n,
        "p_value": p,
        "significant": p < 0.05,
    }


def paired_outcomes(
    rows_a: list[dict[str, Any]],
    rows_b: list[dict[str, Any]],
    *,
    key: str = "case_id",
) -> tuple[int, int, int, int]:
    """Return (both, only_a, only_b, neither) over the cases the two runs share."""
    a = {str(r.get(key)): bool(r.get("passed")) for r in rows_a if not r.get("infra_error")}
    b = {str(r.get(key)): bool(r.get("passed")) for r in rows_b if not r.get("infra_error")}
    shared = sorted(set(a) & set(b))
    both = sum(1 for k in shared if a[k] and b[k])
    only_a = sum(1 for k in shared if a[k] and not b[k])
    only_b = sum(1 for k in shared if b[k] and not a[k])
    neither = len(shared) - both - only_a - only_b
    return both, only_a, only_b, neither


def point_biserial(item: list[float], total: list[float]) -> float | None:
    """Correlation between one case's outcomes and overall scores across the same runs.

    Near zero means the case is noise: solving it says nothing about how capable the
    configuration is, so it contributes nothing to separating them.
    """
    n = len(item)
    if n < 2 or len(total) != n:
        return None
    mean_i = sum(item) / n
    mean_t = sum(total) / n
    cov = sum((x - mean_i) * (y - mean_t) for x, y in zip(item, total, strict=True))
    var_i = sum((x - mean_i) ** 2 for x in item)
    var_t = sum((y - mean_t) ** 2 for y in total)
    if var_i <= 0 or var_t <= 0:
        return None
    return cov / math.sqrt(var_i * var_t)


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
