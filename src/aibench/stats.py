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


def item_rest_correlation(
    item: list[float],
    run_passes: list[float],
    run_measured: list[int],
) -> float | None:
    """Correlate one case's per-run outcome with each run's ability *measured on the others*.

    Two corrections, and they have to happen together because either alone is wrong.

    Correlating a case against a total it belongs to guarantees a positive floor: for a case
    carrying no signal the zero distribution centres near ``1/sqrt(k)``, which at k=31 is 0.18 —
    above the 0.15 threshold meant to reject exactly such a case. Leaving the item out is the
    standard fix.

    The total also has to be a rate, not a count. A run that lost rows to infra errors solved
    fewer cases for a reason that has nothing to do with capability, and a raw count reads that
    outage as weakness: on ``runs/calibration_20260809_231654`` the per-run pass counts are
    ``[26,25,24, 7,5,6, 24,24,24]`` while the rows each run actually produced are
    ``[30,29,29, 9,9,9, 31,31,31]`` — the middle anchor looks four times weaker than the
    weakest one, and its pass *rate* is 0.78/0.56/0.67, in line with the rest.

    Taking counts and doing the arithmetic here is deliberate: a caller passing a rate to a
    routine that subtracts a 0/1 outcome from it gets a plausible number out of mismatched
    units, and nothing in the signature would say so.
    """
    if not (len(item) == len(run_passes) == len(run_measured)):
        return None
    rest: list[float] = []
    for outcome, passes, measured in zip(item, run_passes, run_measured, strict=True):
        if measured <= 1:
            return None  # nothing left once this case is removed
        rest.append((passes - outcome) / (measured - 1))
    return point_biserial(item, rest)


def point_biserial(item: list[float], total: list[float]) -> float | None:
    """Correlation between one case's outcomes and overall scores across the same runs.

    Near zero means the case is noise: solving it says nothing about how capable the
    configuration is, so it contributes nothing to separating them.

    Callers scoring a case against a total that includes it want
    :func:`item_rest_correlation`.
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


def normal_quantile(p: float) -> float:
    """Inverse standard-normal CDF (Acklam's rational approximation, |error| < 1.2e-9).

    Inlined rather than pulled from scipy: it is the only distribution function the sample-size
    planner needs, and scipy is not otherwise a dependency of this harness.
    """
    if not 0.0 < p < 1.0:
        raise ValueError(f"quantile needs 0 < p < 1, got {p}")
    a = (
        -39.69683028665376,
        220.9460984245205,
        -275.9285104469687,
        138.3577518672690,
        -30.66479806614716,
        2.506628277459239,
    )
    b = (
        -54.47609879822406,
        161.5858368580409,
        -155.6989798598866,
        66.80131188771972,
        -13.28068155288572,
    )
    c = (
        -0.007784894002430293,
        -0.3223964580411365,
        -2.400758277161838,
        -2.549732539343734,
        4.374664141464968,
        2.938163982698783,
    )
    d = (0.007784695709041462, 0.3224671290700398, 2.445134137142996, 3.754408661907416)
    plow, phigh = 0.02425, 1 - 0.02425
    if p < plow:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / (
            (((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1
        )
    if p > phigh:
        q = math.sqrt(-2 * math.log(1 - p))
        return -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / (
            (((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1
        )
    q = p - 0.5
    r = q * q
    return (
        (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5])
        * q
        / (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1)
    )


def mcnemar_sample_size(
    *,
    delta: float,
    discordance: float,
    alpha: float = 0.05,
    power: float = 0.8,
) -> dict[str, Any]:
    """Cases needed for a paired test to detect a ``delta`` difference in success rate.

    ``delta`` is the gap between the two success rates and ``discordance`` the share of cases
    they disagree on. Only discordant cases carry information, and the two quantities interact:
    at a fixed ``delta``, more discordance means the disagreement is less one-sided — noisier
    evidence — so the requirement grows.
    """
    if not 0 < delta < 1:
        raise ValueError(f"delta must be a proportion in (0, 1), got {delta}")
    if not 0 < discordance <= 1:
        raise ValueError(f"discordance must be in (0, 1], got {discordance}")
    if discordance <= delta:
        raise ValueError(
            f"discordance ({discordance}) must exceed delta ({delta}): the two runs cannot "
            "differ by more than they disagree"
        )
    z_a = normal_quantile(1 - alpha / 2)
    z_b = normal_quantile(power)
    n = ((z_a * math.sqrt(discordance) + z_b * math.sqrt(discordance - delta**2)) ** 2) / (delta**2)
    n_cases = math.ceil(n)
    return {
        "delta": delta,
        "discordance": discordance,
        "alpha": alpha,
        "power": power,
        "required_cases": n_cases,
        "expected_discordant_pairs": math.ceil(n_cases * discordance),
    }


def observed_discordance(pairwise: list[dict[str, Any]]) -> float | None:
    """Discordance rate seen in an ablation's pairwise comparisons, for planning the next run.

    Self-repeats are excluded. A repeat matrix McNemars every row against `*-r1`, so two runs of
    the *same* configuration appear as a comparison — and their discordance is the harness's own
    run-to-run noise, not a difference between configurations. Averaging that into the planner
    understates the sample size a real comparison needs, because noise-only pairs are the ones
    with the most discordant cases: measured on this corpus, 25.8%–32.3% of cases flip between
    identical repeats.
    """
    comparable = [p for p in pairwise if not p.get("self_repeat") and p.get("comparable", True)]
    totals = [
        (
            p.get("discordant") or 0,
            (p.get("discordant") or 0) + (p.get("both_passed") or 0) + (p.get("neither") or 0),
        )
        for p in (comparable or pairwise)
    ]
    usable = [(d, n) for d, n in totals if n]
    if not usable:
        return None
    return sum(d for d, _ in usable) / sum(n for _, n in usable)


def budget_quantiles(
    case_results: list[dict[str, Any]],
    *,
    fractions: tuple[float, ...] = (0.25, 0.5, 0.75, 0.9, 1.0),
) -> list[int]:
    """Token-budget rungs taken from the observed per-case spend.

    Derived from the data rather than hard-coded so the rungs stay meaningful across case sets
    of different sizes. Callers comparing configurations must pass one shared rung list —
    per-configuration rungs would put each curve on its own x-axis and make them incomparable.
    """
    spends = sorted(
        int(r.get("total_tokens") or 0) for r in case_results if not r.get("infra_error")
    )
    if not spends:
        return []
    rungs = []
    for f in fractions:
        idx = min(len(spends) - 1, max(0, math.ceil(f * len(spends)) - 1))
        rungs.append(spends[idx])
    return sorted(set(rungs))


def cost_curve(
    case_results: list[dict[str, Any]],
    *,
    budgets: list[int],
) -> list[dict[str, Any]]:
    """Success rate achievable if each case were capped at a per-case token budget.

    Two configurations at the same accuracy are not equally good: the one that got there on
    fewer tokens is stronger. A single total-token number hides that, because it mixes cases
    solved cheaply with cases that burned budget and failed anyway.
    """
    effective = [r for r in case_results if not r.get("infra_error")]
    n = len(effective)
    out: list[dict[str, Any]] = []
    for b in budgets:
        solved = sum(
            1 for r in effective if r.get("passed") and int(r.get("total_tokens") or 0) <= b
        )
        out.append(
            {
                "budget_tokens": b,
                "solved": solved,
                "success_rate": (solved / n) if n else 0.0,
            }
        )
    return out


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
