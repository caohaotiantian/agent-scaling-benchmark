"""Empirical calibration: measure what each case actually discriminates, then select.

The tier invariants in :mod:`aibench.tiers` guarantee a case has the *shape* that should make
it hard. Only running it says whether it is. Calibration runs a small panel of anchor
configurations several times over a case set and keeps the cases that behave like useful test
items:

* ``p_hat`` too high — everyone solves it; it separates nothing and only inflates the score.
* ``p_hat`` too low — nobody solves it. Usually a broken case rather than a hard one, which is
  why :func:`aibench.validity.check_reference_solution` runs first.
* ``spread`` near zero — every configuration performs identically on it.
* ``r_pb`` near zero — its outcome is uncorrelated with overall ability, i.e. noise.

Aggregation is deliberately separated from execution: :func:`aggregate_calibration` is a pure
function over result rows, so the selection policy is testable without spending a single call.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from aibench.cases import case_set_dir, is_case_json_path
from aibench.io_util import load_json, load_yaml, repo_root, write_json, write_text
from aibench.stats import item_rest_correlation, wilson_ci

DEFAULT_P_MAX = 0.9
DEFAULT_P_MIN = 0.05
DEFAULT_MIN_RPB = 0.15


@dataclass
class AnchorSpec:
    """One configuration in the calibration panel."""

    name: str
    agent_config: str
    model_config: str
    run_config: str | None = None

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> AnchorSpec:
        return cls(
            name=str(d.get("name") or d.get("experiment_name") or "anchor"),
            agent_config=str(d.get("agent_config") or "configs/agents/openai_compat.yaml"),
            model_config=str(d.get("model_config") or "configs/models/glm52.yaml"),
            run_config=d.get("run_config"),
        )


@dataclass
class SelectionPolicy:
    p_max: float = DEFAULT_P_MAX
    p_min: float = DEFAULT_P_MIN
    min_rpb: float = DEFAULT_MIN_RPB

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CaseCalibration:
    case_id: str
    tier: str | None
    attempts: int
    passes: int
    p_hat: float
    confidence_interval: str | None
    #: Content hash of the case as measured; a later run reuses this result only if it matches.
    fingerprint: str | None = None
    by_anchor: dict[str, float] = field(default_factory=dict)
    #: How many attempts each anchor actually contributed. `by_anchor` alone is a rate with no
    #: denominator, so a published Δ between two anchors could not be recomputed under the
    #: convention the calibrations README promises — an anchor that produced 3 of 9 attempts and
    #: one that produced 9 read identically.
    by_anchor_attempts: dict[str, int] = field(default_factory=dict)
    #: Whether the case ships a reference solution. `auto-v0`'s published row requires
    #: "只取有参考解的 105 条" and no field in the file identified which 105, so the recompute
    #: recipe failed on its own first and most-cited example: following it naively lands on
    #: 62.7 / 13.5 / 23.8 against a published 75.2 / 16.2 / 8.6.
    has_reference: bool | None = None
    spread: float = 0.0
    point_biserial: float | None = None
    flaky: bool = False
    keep: bool = True
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


#: Bumped when the basis below changes, and carried inside the value. A stored fingerprint from
#: an older scheme can then never compare equal to one computed now, so the reuse gate rejects
#: it instead of handing back a p_hat measured by code that no longer exists.
ANCHOR_FINGERPRINT_VERSION = "v2"


def anchor_fingerprint(anchors: list[AnchorSpec]) -> str:
    """Identity of the panel a calibration was measured against.

    Includes the *contents* of each referenced config, not just its path: swapping the model
    inside `glm52.yaml` changes what the anchors mean while every path stays the same, and a
    p_hat measured against the old panel would silently keep being trusted.

    It also includes the harness source that decides what a run means. Hashing only the three
    YAML files left the panel byte-identical across an adapter fix that moved the same model's
    pass rate by 58 points — the same ``5f5233c7214879f4`` before and after — so
    ``--reuse-from`` would have carried the old numbers straight through a change of behaviour.
    Config files say which agent and model; they say nothing about how the agent is driven.
    """
    from aibench.provenance import harness_digest

    root = repo_root()
    parts: list[str] = [harness_digest()]
    for a in sorted(anchors, key=lambda x: x.name):
        parts.append(a.name)
        for rel in (a.agent_config, a.model_config, a.run_config):
            path = _abs(root, rel)
            parts.append(path.read_text(encoding="utf-8") if path and path.is_file() else str(rel))
    digest = hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()[:16]
    return f"{ANCHOR_FINGERPRINT_VERSION}:{digest}"


def unfit_anchors(
    anchors: list[AnchorSpec],
    tiers: set[str],
) -> list[tuple[str, str, list[str]]]:
    """Anchors that cannot exercise a tier present in the set, as (anchor, tier, missing axes).

    Measured: composing retrieval cases raised the weak *single-turn* anchor by 24pp, because
    that scaffold pastes every file into the prompt — the distractors were not an obstacle to
    search past, they were extra working code handed over for free. A panel member that cannot
    exhibit an axis does not score low on it; it scores something unrelated, and the spread
    computed across such a panel is not a measurement of that capability.
    """
    from aibench.io_util import load_yaml
    from aibench.models import AgentConfig
    from aibench.tiers import tier_spec

    root = repo_root()
    problems: list[tuple[str, str, list[str]]] = []
    for anchor in anchors:
        path = _abs(root, anchor.agent_config)
        if path is None or not path.is_file():
            continue
        declared = AgentConfig.from_dict(load_yaml(path)).capability_axes
        if not declared:
            continue  # undeclared means unknown, not unfit
        for tier in sorted(tiers):
            try:
                required = tier_spec(tier).axes
            except ValueError:
                continue
            missing = [a for a in required if a not in declared]
            if missing:
                problems.append((anchor.name, tier, missing))
    return problems


def plan_calibration(
    case_ids: list[str],
    fingerprints: dict[str, str],
    previous: dict[str, Any] | None,
    *,
    panel: str,
    never_reuse: set[str] | None = None,
) -> tuple[list[str], list[dict[str, Any]]]:
    """Split cases into those needing a run and those whose earlier result still holds.

    A previous result is reusable only when both the case and the panel are byte-identical to
    what produced it; either changing invalidates the measurement.

    Results recorded under an older fingerprint scheme are never reused. Those fingerprints
    were computed over the prompt and file paths alone, so they cannot witness a change of
    file contents, and accepting one would return a p_hat measured on code that no longer
    exists. ``never_reuse`` carries the same argument one step further, for cases whose code
    lives outside the JSON — no fingerprint computed from the case file can witness an edit to
    a snapshot or a clone.
    """
    from aibench.validity import FINGERPRINT_VERSION

    if not previous or previous.get("anchor_fingerprint") != panel:
        return list(case_ids), []
    current = f"{FINGERPRINT_VERSION}:"
    relevant = [c for c in previous.get("cases") or [] if c.get("case_id") in fingerprints]
    stale = sum(
        1
        for c in relevant
        if c.get("fingerprint") and not str(c["fingerprint"]).startswith(current)
    )
    unfingerprinted = sum(1 for c in relevant if not c.get("fingerprint"))
    if stale:
        print(
            f"ignoring {stale} earlier result(s) recorded under an older fingerprint scheme; "
            "they cannot show whether the case contents changed"
        )
    if unfingerprinted:
        print(f"ignoring {unfingerprinted} earlier result(s) that carry no fingerprint")
    prior = {
        c["case_id"]: c
        for c in relevant
        if c.get("fingerprint") and str(c["fingerprint"]).startswith(current)
    }
    blocked = never_reuse or set()
    reusable = [
        prior[cid]
        for cid in case_ids
        if cid in prior
        and cid not in blocked
        and prior[cid].get("fingerprint") == fingerprints.get(cid)
    ]
    reused_ids = {c["case_id"] for c in reusable}
    return [cid for cid in case_ids if cid not in reused_ids], reusable


#: Reason code for a case the panel did not fully measure. Kept apart from the quality reasons
#: on purpose: it says *our measurement* is incomplete, and the remedy is to re-run, not to
#: discard the case. Folding it in with `too_easy` and friends is how a half-finished sweep gets
#: read as a batch of bad cases — the same confusion the uncollectable/hard split exists to stop.
INCOMPLETE_PANEL = "incomplete_panel"


def verdict_reasons(
    p_hat: float,
    r_pb: float | None,
    policy: SelectionPolicy,
    *,
    anchors_measured: int | None = None,
    anchors_expected: int | None = None,
) -> list[str]:
    """Why this case would be dropped, under ``policy``. Empty means keep.

    A pure function of the measurement and the thresholds, so a reused result can be re-judged
    without re-running anything.

    A case the panel did not fully cover is blocked before any quality judgement: its ``spread``
    and ``r_pb`` were computed across a different panel than the report names, so comparing them
    against thresholds calibrated on the full panel is a category error.
    """
    if anchors_expected and anchors_measured is not None and anchors_measured < anchors_expected:
        return [f"{INCOMPLETE_PANEL}({anchors_measured}/{anchors_expected} anchors) — re-run"]

    reasons: list[str] = []
    if p_hat > policy.p_max:
        reasons.append(f"too_easy(p={p_hat:.2f}>{policy.p_max})")
    if p_hat < policy.p_min:
        reasons.append(f"unsolved_by_all(p={p_hat:.2f}<{policy.p_min}) — verify the case itself")
    if r_pb is not None and r_pb < policy.min_rpb and policy.p_min <= p_hat <= policy.p_max:
        reasons.append(f"no_discrimination(r_pb={r_pb:.2f}<{policy.min_rpb})")
    return reasons


def anchor_coverage(
    configured: list[str],
    runs: list[dict[str, Any]],
    *,
    cases: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Which of the configured anchors the report's numbers actually rest on.

    A calibration that lost passes — a killed process, a gateway refusing every request for
    one anchor — still writes a report, and nothing in the numbers says the panel was smaller
    than intended. Pairing such a result against a complete one reads as a real effect and is
    not one; this records the difference so it cannot be missed.

    ``cases`` is the merged case list, which includes results carried over by ``--reuse-from``.
    Those cases were measured by anchors that this invocation never ran, so judging coverage on
    ``runs`` alone would report a fully reused calibration as having no anchors at all — and a
    field that cries wolf on a complete panel is worse than no field.
    """
    rows_by_anchor: dict[str, int] = {}
    for run in runs:
        name = str(run.get("anchor", "anchor"))
        rows_by_anchor[name] = rows_by_anchor.get(name, 0) + len(run.get("rows") or [])
    produced = {name for name, count in rows_by_anchor.items() if count}
    for case in cases or []:
        produced.update(str(k) for k in (case.get("by_anchor") or {}))
    return {
        "anchors_configured": sorted(configured),
        "anchors_with_rows_this_run": dict(sorted(rows_by_anchor.items())),
        "anchors_of_record": sorted(produced),
        "anchors_missing": sorted(set(configured) - produced),
    }


def load_anchor_panel(path: Path) -> tuple[list[AnchorSpec], dict[str, Any]]:
    data = load_yaml(path)
    raw = data.get("anchors") or data.get("runs") or []
    if not raw:
        raise ValueError(f"anchor panel {path} has no `anchors:` list")
    return [AnchorSpec.from_dict(a) for a in raw], data


def aggregate_calibration(
    runs: list[dict[str, Any]],
    *,
    policy: SelectionPolicy | None = None,
) -> dict[str, Any]:
    """Turn per-run result rows into per-case item statistics and a keep/drop verdict.

    ``runs`` is a list of ``{"anchor": name, "rows": [result row, ...]}``. Rows are the same
    shape ``runner`` writes to ``results.jsonl``; infra errors are excluded, since a sandbox
    failure is not evidence about the case.
    """
    pol = policy or SelectionPolicy()

    outcomes: dict[str, dict[int, list[bool]]] = {}
    tiers: dict[str, str | None] = {}
    fingerprints: dict[str, str | None] = {}
    has_reference: dict[str, bool | None] = {}
    anchor_of: list[str] = []
    for i, run in enumerate(runs):
        anchor_of.append(str(run.get("anchor", "anchor")))
        for row in run.get("rows") or []:
            if row.get("infra_error"):
                continue
            cid = str(row.get("case_id") or "")
            if not cid:
                continue
            tiers.setdefault(cid, row.get("tier"))
            fingerprints.setdefault(cid, row.get("fingerprint"))
            has_reference.setdefault(cid, row.get("has_reference"))
            outcomes.setdefault(cid, {}).setdefault(i, []).append(bool(row.get("passed")))

    run_indices = list(range(len(runs)))
    runs_of_anchor: dict[str, list[int]] = {}
    for i, name in enumerate(anchor_of):
        runs_of_anchor.setdefault(name, []).append(i)

    # Ability per run, as a pass rate over the cases that run actually measured. A raw count
    # would read an outage as weakness: the middle anchor of
    # `runs/calibration_20260809_231654` produced 9 rows of 31 per repeat, so its pass count is
    # a third of the others while its pass rate is in line with them.
    run_passes = [
        float(sum(1 for per_run in outcomes.values() for ok in per_run.get(i, []) if ok))
        for i in run_indices
    ]
    run_measured = [sum(1 for per_run in outcomes.values() if per_run.get(i)) for i in run_indices]

    reports: list[CaseCalibration] = []
    for cid in sorted(outcomes):
        per_run = outcomes[cid]
        flat = [ok for results in per_run.values() for ok in results]
        attempts = len(flat)
        passes = sum(1 for ok in flat if ok)
        p_hat = passes / attempts if attempts else 0.0

        # Group across an anchor's repeats: an anchor that solves a case only sometimes is the
        # signal that the case itself is unstable, and one run per anchor can never show it.
        by_anchor: dict[str, float] = {}
        by_anchor_attempts: dict[str, int] = {}
        flaky = False
        for name, indices in runs_of_anchor.items():
            hits = [ok for i in indices for ok in per_run.get(i, [])]
            if not hits:
                continue
            rate = sum(1 for ok in hits if ok) / len(hits)
            by_anchor[name] = rate
            by_anchor_attempts[name] = len(hits)
            flaky = flaky or 0.0 < rate < 1.0
        spread = (max(by_anchor.values()) - min(by_anchor.values())) if by_anchor else 0.0

        # Only the runs that actually produced a row for this case. Scoring a missing run as
        # 0.0 made the case look failed exactly where the run also scored low overall — a
        # correlation with the outage rather than with ability. Measured on
        # `runs/calibration_20260809_231654`: 11 of the 13 kept cases had incomplete attempts,
        # among them `rev-4646d93ae250add0` at attempts 6/9, p_hat 1.00 and r_pb 0.996.
        measured = [i for i in run_indices if per_run.get(i)]
        item = [sum(1 for ok in per_run[i] if ok) / len(per_run[i]) for i in measured]
        r_pb = item_rest_correlation(
            item, [run_passes[i] for i in measured], [run_measured[i] for i in measured]
        )

        reasons = verdict_reasons(
            p_hat,
            r_pb,
            pol,
            anchors_measured=len(by_anchor),
            anchors_expected=len(runs_of_anchor),
        )

        ci = wilson_ci(passes, attempts)
        reports.append(
            CaseCalibration(
                case_id=cid,
                tier=tiers.get(cid),
                fingerprint=fingerprints.get(cid),
                attempts=attempts,
                passes=passes,
                p_hat=p_hat,
                confidence_interval=f"[{ci[0] * 100:.1f}%, {ci[1] * 100:.1f}%]" if ci else None,
                by_anchor=by_anchor,
                by_anchor_attempts=by_anchor_attempts,
                has_reference=has_reference.get(cid),
                spread=spread,
                point_biserial=r_pb,
                flaky=flaky,
                keep=not reasons,
                reasons=reasons,
            )
        )

    kept = [r for r in reports if r.keep]
    return {
        "policy": pol.to_dict(),
        "anchors": sorted({str(r.get("anchor", "anchor")) for r in runs}),
        "run_count": len(runs),
        "total_cases": len(reports),
        "kept_count": len(kept),
        "dropped_count": len(reports) - len(kept),
        "p_hat_distribution": _p_buckets(reports),
        "kept_p_hat_distribution": _p_buckets(kept),
        "tier_distribution": _tier_counts(kept),
        # Coverage is reported next to the verdicts, not inferred from them. A sweep that lost
        # passes still writes a report, and nothing in the numbers used to say the panel was
        # smaller than intended. `run_count` above already carries how many passes were made.
        "incomplete_panel_count": sum(
            1 for r in reports if any(x.startswith(INCOMPLETE_PANEL) for x in r.reasons)
        ),
        "rows_dropped_by_anchor": _dropped_by_anchor(runs),
        "cases": [r.to_dict() for r in reports],
    }


def _dropped_by_anchor(runs: list[dict[str, Any]]) -> dict[str, int]:
    """Rows each anchor lost to infra errors — the usual reason a panel comes back partial."""
    out: dict[str, int] = {}
    for run in runs:
        name = str(run.get("anchor", "anchor"))
        lost = sum(1 for row in run.get("rows") or [] if row.get("infra_error"))
        if lost:
            out[name] = out.get(name, 0) + lost
    return dict(sorted(out.items()))


def _p_buckets(reports: list[CaseCalibration]) -> dict[str, int]:
    return _p_buckets_from_rows([{"p_hat": r.p_hat} for r in reports])


def _p_buckets_from_rows(rows: list[dict[str, Any]]) -> dict[str, int]:
    buckets = {"0.0-0.2": 0, "0.2-0.5": 0, "0.5-0.8": 0, "0.8-1.0": 0}
    for r in rows:
        p = float(r.get("p_hat") or 0.0)
        if p < 0.2:
            buckets["0.0-0.2"] += 1
        elif p < 0.5:
            buckets["0.2-0.5"] += 1
        elif p < 0.8:
            buckets["0.5-0.8"] += 1
        else:
            buckets["0.8-1.0"] += 1
    return buckets


def _tier_counts(reports: list[CaseCalibration]) -> dict[str, int]:
    return _count_by_tier([{"tier": r.tier} for r in reports])


def _count_by_tier(rows: list[dict[str, Any]]) -> dict[str, int]:
    out: dict[str, int] = {}
    for r in rows:
        key = str(r.get("tier") or "unset")
        out[key] = out.get(key, 0) + 1
    return dict(sorted(out.items()))


def calibrate_case_set(
    case_set: str,
    anchors: list[AnchorSpec],
    *,
    repeats: int = 3,
    output_root: Path | None = None,
    policy: SelectionPolicy | None = None,
    case_workers: int | None = None,
    reuse_from: Path | None = None,
    parallel: int = 1,
    allow_unfit_anchors: bool = False,
) -> tuple[Path, dict[str, Any]]:
    """Run the anchor panel ``repeats`` times over ``case_set`` and write ``calibration.json``.

    ``reuse_from`` points at an earlier ``calibration.json``; cases whose content and panel are
    unchanged keep their previous result and are not re-run. A calibration costs
    anchors x repeats full passes, so re-measuring a set after adding a handful of cases is
    otherwise the most expensive no-op in the pipeline.
    """
    from aibench.runner import run_benchmark

    root = repo_root()
    out_root = output_root or (root / "runs")
    cal_dir = out_root / f"calibration_{time.strftime('%Y%m%d_%H%M%S')}"
    cal_dir.mkdir(parents=True, exist_ok=True)
    panel = anchor_fingerprint(anchors)

    from aibench.cases import load_cases
    from aibench.validity import case_fingerprint, external_workspace

    cases = load_cases(case_set, validate=True)
    tiers_present = {c.tier for c in cases if c.tier}
    unfit = unfit_anchors(anchors, tiers_present)
    if unfit and not allow_unfit_anchors:
        lines = "\n".join(
            f"  {name} cannot exercise {tier} (missing {', '.join(missing)})"
            for name, tier, missing in unfit
        )
        raise ValueError(
            f"anchor panel cannot measure the tiers in {case_set!r}:\n{lines}\n"
            "A panel member that cannot exhibit an axis does not score low on it, it scores "
            "something unrelated, so the resulting spread is not a measurement of that "
            "capability. Use a panel whose members can all exercise these tiers, or pass "
            "allow_unfit_anchors to record the numbers anyway."
        )
    # Always recomputed. `metadata.fingerprint` is whatever the last `audit-cases --annotate`
    # left behind, so trusting it makes reuse depend on when the annotation was run rather
    # than on what the case now contains.
    fingerprints = {c.case_id: case_fingerprint(c) for c in cases}
    # A snapshot- or git-backed case keeps its fingerprint when the snapshot changes, because
    # the fingerprint is computed from the case file and the code is not in it.
    external = {c.case_id for c in cases if external_workspace(c)}
    previous = load_json(reuse_from) if reuse_from and reuse_from.is_file() else None
    todo, reused = plan_calibration(
        [c.case_id for c in cases], fingerprints, previous, panel=panel, never_reuse=external
    )
    if reused:
        print(f"reusing {len(reused)} unchanged case results; running {len(todo)}")

    run_set = case_set
    if reused and todo:
        run_set = _materialize_subset(case_set, todo)
    elif reused and not todo:
        run_set = None  # nothing changed; the previous numbers still stand

    runs: list[dict[str, Any]] = []
    if run_set is not None:
        jobs = [(a, rep) for a in anchors for rep in range(1, repeats + 1)]

        def _one_pass(job: tuple[AnchorSpec, int]) -> dict[str, Any]:
            anchor, rep = job
            run_dir = run_benchmark(
                run_config_path=_abs(root, anchor.run_config),
                agent_config_path=_abs(root, anchor.agent_config),
                model_config_path=_abs(root, anchor.model_config),
                case_set=run_set,
                run_id=f"cal-{anchor.name}-r{rep}",
                output_root=cal_dir,
                case_workers=case_workers,
            )
            return {
                "anchor": anchor.name,
                "repeat": rep,
                "rows": read_result_rows(run_dir / "results.jsonl"),
            }

        # Passes are independent — separate run directories, separate workspaces — so the only
        # reason to serialise them is upstream capacity. Each pass already runs `case_workers`
        # cases at once, so the concurrency the gateway actually sees is the product.
        if parallel <= 1:
            runs = [_one_pass(j) for j in jobs]
        else:
            with ThreadPoolExecutor(max_workers=parallel) as ex:
                runs = list(ex.map(_one_pass, jobs))

    report = aggregate_calibration(runs, policy=policy)
    if reused:
        report = _merge_reused(
            report,
            reused,
            policy=policy,
            tiers={c.case_id: c.tier for c in cases},
            anchors_expected=len(anchors),
        )
    from aibench.provenance import environment
    from aibench.validity import set_fingerprint

    report["case_set"] = case_set
    report["repeats"] = repeats
    report["anchor_fingerprint"] = panel
    # None of the 13 published calibrations records what produced it, and the 24 run directories
    # behind them stamp the literal `aibench@0.1.0 / agent@1.0.0` — a constant that
    # `provenance.py` now calls "worse than no field", because it reads as an answer. The
    # README warns that adapter defects moved one model's pass rate 58 points; a reader holding
    # these numbers could not tell which side of which fix any of them sat on. The numbers were
    # arithmetically checkable and not attributable.
    report["provenance"] = environment()
    try:
        report["case_set_fingerprint"] = set_fingerprint(cases)
    except Exception:
        report["case_set_fingerprint"] = None
    report["unfit_anchors"] = [{"anchor": n, "tier": t, "missing_axes": m} for n, t, m in unfit]
    report["reused_case_count"] = len(reused)
    report["recalibrated_case_count"] = len(todo)
    coverage = anchor_coverage([a.name for a in anchors], runs, cases=report.get("cases") or [])
    report.update(coverage)
    missing = coverage["anchors_missing"]
    if missing:
        print(
            f"WARNING: {len(missing)} configured anchor(s) produced no rows: {', '.join(missing)}. "
            "These numbers describe a smaller panel than the one configured; do not compare them "
            "against a full-panel calibration."
        )
    write_json(cal_dir / "calibration.json", report)
    write_text(cal_dir / "calibration_report.md", render_calibration_md(report))
    return cal_dir, report


def _materialize_subset(case_set: str, case_ids: list[str]) -> str:
    """Write a temporary case set holding only ``case_ids`` so a run can cover just those."""
    src = case_set_dir(case_set)
    dest_name = f".calibrating-{case_set}"
    dest = repo_root() / "benchmarks/ai_coding/cases" / dest_name
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True, exist_ok=True)
    wanted = set(case_ids)
    for p in sorted(src.glob("*.json")):
        if is_case_json_path(p) and load_json(p).get("case_id") in wanted:
            shutil.copy2(p, dest / p.name)
    snap = src / "snapshots"
    if snap.is_dir():
        shutil.copytree(snap, dest / "snapshots")
    return dest_name


def _merge_reused(
    report: dict[str, Any],
    reused: list[dict[str, Any]],
    *,
    policy: SelectionPolicy | None,
    tiers: dict[str, str | None] | None = None,
    anchors_expected: int | None = None,
) -> dict[str, Any]:
    """Fold previously measured cases back in and recompute the set-level distributions.

    The measurement is reused; the *verdict* is not. ``keep`` and ``reasons`` are recomputed
    against the current policy, because tuning ``p_max``/``p_min``/``min_rpb`` needs no
    re-measurement and is therefore exactly when an operator reaches for ``--reuse-from`` —
    which used to hand back the previous run's keep/drop decisions under a report that stated
    the new thresholds.

    ``tiers`` refreshes the tier label for the same reason: it is deliberately outside the
    fingerprint because it cannot change p_hat, so a retagged case would otherwise keep its old
    label in the tier quota that decides what ships.
    """
    pol = policy or SelectionPolicy()
    for row in reused:
        if tiers is not None and row.get("case_id") in tiers:
            row["tier"] = tiers[row["case_id"]]
        p_hat = row.get("p_hat")
        if p_hat is None:
            continue
        # Coverage travels with the reused row: a case carried over from a partial sweep is
        # still a case the panel did not fully measure, and re-judging it without that fact
        # would launder an incomplete measurement into a clean verdict.
        by_anchor = row.get("by_anchor") or {}
        row["reasons"] = verdict_reasons(
            float(p_hat),
            row.get("point_biserial"),
            pol,
            anchors_measured=len(by_anchor),
            anchors_expected=anchors_expected,
        )
        row["keep"] = not row["reasons"]
    cases = [*(report.get("cases") or []), *reused]
    cases.sort(key=lambda c: str(c.get("case_id")))
    kept = [c for c in cases if c.get("keep")]
    merged = dict(report)
    merged.update(
        {
            "policy": (policy or SelectionPolicy()).to_dict(),
            "total_cases": len(cases),
            "kept_count": len(kept),
            "dropped_count": len(cases) - len(kept),
            "p_hat_distribution": _p_buckets_from_rows(cases),
            "kept_p_hat_distribution": _p_buckets_from_rows(kept),
            "tier_distribution": _count_by_tier(kept),
            # Recomputed for the same reason as the counts above: a reused row can be judged
            # `incomplete_panel` here, and a report that drops it while claiming zero
            # incomplete cases contradicts itself.
            "incomplete_panel_count": sum(
                1
                for c in cases
                if any(str(x).startswith(INCOMPLETE_PANEL) for x in c.get("reasons") or [])
            ),
            "cases": cases,
        }
    )
    return merged


def read_result_rows(path: Path) -> list[dict[str, Any]]:
    """Read a run's ``results.jsonl`` into per-case rows."""
    if not path.is_file():
        return []
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


def _abs(root: Path, rel: str | None) -> Path | None:
    if not rel:
        return None
    p = Path(rel)
    return p if p.is_absolute() else root / p


def parse_tier_quota(spec: str | None) -> dict[str, float]:
    """Parse ``T2=0.3,T3=0.4`` into per-tier shares of the selected set."""
    if not spec:
        return {}
    out: dict[str, float] = {}
    for part in spec.split(","):
        if not part.strip():
            continue
        tier, _, share = part.partition("=")
        if not share:
            raise ValueError(f"tier quota needs TIER=SHARE, got {part!r}")
        out[tier.strip()] = float(share)
    return out


def _rank(case: dict[str, Any]) -> tuple[float, float]:
    return (-(case.get("spread") or 0.0), -(case.get("point_biserial") or 0.0))


#: Band edges for difficulty quotas, in p_hat. A case above ``easy`` is solved by nearly every
#: anchor and separates nothing; one below ``hard`` is solved by nearly none. These are for
#: *shaping the set*, and are deliberately distinct from ``SelectionPolicy``'s keep/drop
#: thresholds, which decide whether a case is usable at all.
DIFFICULTY_BANDS = {"hard": 0.2, "easy": 0.8}


#: The bands a quota may name. ``unmeasured`` is reachable but never quota-fillable.
DIFFICULTY_BAND_NAMES = ("hard", "mid", "easy")


def difficulty_band(p_hat: float | None) -> str:
    """Which band a measured case falls in: ``easy`` / ``mid`` / ``hard`` / ``unmeasured``.

    A case with no p_hat is *not* mid. Calling it mid would let an uncalibrated case fill the
    quota that carries the set's whole claim to discriminating, and then be counted in the
    achieved distribution as though it had been measured.
    """
    if p_hat is None:
        return "unmeasured"
    if p_hat < DIFFICULTY_BANDS["hard"]:
        return "hard"
    if p_hat > DIFFICULTY_BANDS["easy"]:
        return "easy"
    return "mid"


def _validate_shares(shares: dict[str, float], label: str) -> None:
    bad = {k: v for k, v in shares.items() if v < 0 or v > 1}
    if bad:
        raise ValueError(f"{label} shares must be between 0 and 1, got {bad}")
    if abs(sum(shares.values()) - 1.0) > 0.01:
        raise ValueError(f"{label} shares sum to {sum(shares.values()):.2f}, expected 1.0")


def _largest_remainder(shares: dict[str, float], total: int, *, rotate: int = 0) -> dict[str, int]:
    """Split ``total`` across ``shares`` so the parts sum to exactly ``total``.

    Rounding each share independently does not add up: three bands at 0.15/0.70/0.15 of 10
    round to 2+7+2 = 11, and three equal thirds of 10 round to 9. Either way the operator gets
    a count they did not ask for, with nothing reporting the difference.

    ``rotate`` shifts which key wins a tied remainder. Breaking ties by name alone is
    deterministic but biased: with four equal tier shares the last name alphabetically loses
    the remainder in every band, so it ends up with half the quota it asked for.
    """
    if total <= 0 or not shares:
        return dict.fromkeys(shares, 0)
    exact = {k: v * total for k, v in shares.items()}
    out = {k: int(v) for k, v in exact.items()}
    remainder = max(total - sum(out.values()), 0)
    names = sorted(exact)
    order = names[rotate % len(names) :] + names[: rotate % len(names)]
    rank = {k: i for i, k in enumerate(order)}
    for k in sorted(exact, key=lambda k: (-(exact[k] - int(exact[k])), rank[k]))[:remainder]:
        out[k] += 1
    return out


def apply_difficulty_quota(
    keep: list[dict[str, Any]],
    *,
    quota: dict[str, float],
    max_cases: int | None,
    tier_quota: dict[str, float] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Shape the set by measured difficulty, returning the selection and what it achieved.

    ``SelectionPolicy`` decides which cases are usable; nothing decided what the set should
    look like, so everything it kept was ranked purely by discrimination and the shape fell
    out of whatever the pool happened to contain.

    The shortfall per band is reported rather than quietly back-filled from another band. A set
    that missed its shape is a fact about the pool, and hiding it behind a top-up would make the
    next reader believe a target was met that never was.
    """
    if not quota:
        return (keep[:max_cases] if max_cases is not None else keep), {}

    unknown = sorted(set(quota) - set(DIFFICULTY_BAND_NAMES))
    if unknown:
        raise ValueError(
            f"unknown difficulty band(s) {unknown}; expected {list(DIFFICULTY_BAND_NAMES)}. "
            "A misspelt band would otherwise be reported as a pool shortfall, sending you to "
            "calibrate more cases while the ones you asked for sat unused."
        )
    _validate_shares(quota, "difficulty quota")
    if tier_quota:
        # Unvalidated tier shares overshoot `want`, and the excess was truncated in flag order,
        # silently dropping a whole tier with nothing reported.
        _validate_shares(tier_quota, "tier quota")
    if max_cases is not None and max_cases < 0:
        raise ValueError(f"--max-cases must not be negative, got {max_cases}")

    by_band: dict[str, list[dict[str, Any]]] = {}
    bucketed: set[str] = set()
    for c in keep:
        cid = str(c.get("case_id"))
        if cid in bucketed:
            continue  # one row per case, so a repeated id cannot consume two slots
        bucketed.add(cid)
        by_band.setdefault(difficulty_band(c.get("p_hat")), []).append(c)
    for rows in by_band.values():
        rows.sort(key=_rank)

    total = max_cases if max_cases is not None else len(keep)
    wants = _largest_remainder(quota, total)
    picked: list[dict[str, Any]] = []
    seen: set[str] = set()
    achieved: dict[str, Any] = {
        "target": dict(quota),
        "requested_total": total,
        "bands": {},
        "shortfall": {},
        "tier_shortfall": {},
    }
    for band in quota:
        # Rotation must key off the band's own identity. Taking it from the band's position in
        # the flag string put the order-dependence back where it had just been removed: the
        # same quota typed in a different order selected a different set.
        band_index = DIFFICULTY_BAND_NAMES.index(band)
        want = wants[band]
        available = [c for c in by_band.get(band, []) if str(c.get("case_id")) not in seen]
        take, tier_short = _within_band(available, want, tier_quota, rotate=band_index)
        seen.update(str(c.get("case_id")) for c in take)
        picked.extend(take)
        achieved["bands"][band] = {"wanted": want, "got": len(take), "pool": len(available)}
        if len(take) < want:
            achieved["shortfall"][band] = want - len(take)
        if tier_short:
            achieved["tier_shortfall"][band] = tier_short

    out = sorted(picked, key=_rank)[:total]
    achieved["total"] = len(out)
    # Denominator is what was ASKED for, not what was delivered. Dividing by the delivered
    # count renormalises the shortfall away: a set missing every hard case would report the
    # other bands as on target, and the gap would vanish from the block describing it.
    achieved["actual_shares"] = {
        b: round(v["got"] / total, 4) if total else 0.0 for b, v in achieved["bands"].items()
    }
    achieved["unmeasured_in_pool"] = len(by_band.get("unmeasured", []))
    return out, achieved


def _within_band(
    rows: list[dict[str, Any]],
    want: int,
    tier_quota: dict[str, float] | None,
    *,
    rotate: int = 0,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Best ``want`` cases from one band, plus any per-tier shortfall.

    Allocating each tier with an independent ``round`` overshot ``want`` and was then
    truncated, so whichever tiers came last in the flag lost their picks entirely: the same
    quota typed in a different order produced a different case set, and a tier could be
    starved with nothing reporting it.
    """
    if want <= 0 or not rows:
        return [], {}
    if not tier_quota:
        return rows[:want], {}

    by_tier: dict[str, list[dict[str, Any]]] = {}
    for c in rows:
        by_tier.setdefault(str(c.get("tier") or "unset"), []).append(c)
    wants = _largest_remainder(tier_quota, want, rotate=rotate)
    out: list[dict[str, Any]] = []
    short: dict[str, int] = {}
    for tier, n in wants.items():
        take = by_tier.get(tier, [])[:n]
        out.extend(take)
        if len(take) < n:
            short[tier] = n - len(take)
    if len(out) < want:
        # Top up by rank, not by tier order, so the fill is deterministic.
        chosen = {str(c.get("case_id")) for c in out}
        out.extend([c for c in rows if str(c.get("case_id")) not in chosen][: want - len(out)])
    return out[:want], short


def apply_tier_quota(
    keep: list[dict[str, Any]],
    *,
    quota: dict[str, float],
    max_cases: int | None,
) -> list[dict[str, Any]]:
    """Take the best discriminators per tier instead of globally.

    Ranking purely by discrimination tends to concentrate the set in whichever tier happens to
    separate the current anchor panel best, which then reports a single capability band as if
    it were the whole picture. Quotas keep the coverage the tiers were built for.
    """
    if not quota:
        return keep[:max_cases] if max_cases is not None else keep

    by_tier: dict[str, list[dict[str, Any]]] = {}
    for c in keep:
        by_tier.setdefault(str(c.get("tier") or "unset"), []).append(c)
    for rows in by_tier.values():
        rows.sort(key=_rank)

    total = max_cases if max_cases is not None else len(keep)
    picked: list[dict[str, Any]] = []
    for tier, share in quota.items():
        want = round(share * total)
        picked.extend(by_tier.get(tier, [])[:want])

    # Quotas that under-fill (a tier had fewer good cases than asked for) are topped up with
    # the best remaining cases rather than silently returning a short set.
    if len(picked) < total:
        chosen = {id(c) for c in picked}
        picked.extend([c for c in keep if id(c) not in chosen][: total - len(picked)])
    return sorted(picked, key=_rank)[:total]


def select_cases(
    calibration: dict[str, Any],
    *,
    source_set: str,
    dest_set: str,
    max_cases: int | None = None,
    tier_quota: dict[str, float] | None = None,
    difficulty_quota: dict[str, float] | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Copy the cases calibration kept into a new case set, best discriminators first."""
    keep = [c for c in calibration.get("cases") or [] if c.get("keep")]
    keep.sort(key=_rank)
    difficulty_report: dict[str, Any] = {}
    if difficulty_quota:
        # Difficulty is the outer dimension when asked for: it decides the set's shape, and the
        # tier quota then spreads the picks within each band so capability coverage survives.
        keep, difficulty_report = apply_difficulty_quota(
            keep, quota=difficulty_quota, max_cases=max_cases, tier_quota=tier_quota
        )
    else:
        keep = apply_tier_quota(keep, quota=tier_quota or {}, max_cases=max_cases)
    wanted = {c["case_id"] for c in keep}

    src = case_set_dir(source_set)
    if not src.is_dir():
        raise FileNotFoundError(f"source case set not found: {src}")
    dest = case_set_dir(dest_set)
    if not dry_run:
        dest.mkdir(parents=True, exist_ok=True)

    selected: list[str] = []
    for path in sorted(p for p in src.glob("*.json") if is_case_json_path(p)):
        raw = load_json(path)
        cid = raw.get("case_id")
        if cid not in wanted:
            continue
        stats = next(c for c in keep if c["case_id"] == cid)
        meta = dict(raw.get("metadata") or {})
        meta["calibration"] = {
            "p_hat": stats["p_hat"],
            "spread": stats["spread"],
            "point_biserial": stats["point_biserial"],
            "attempts": stats["attempts"],
        }
        raw["metadata"] = meta
        if not dry_run:
            write_json(dest / f"{cid}.json", raw)
            snap_src = src / "snapshots" / str(cid)
            if snap_src.is_dir():
                snap_dst = dest / "snapshots" / str(cid)
                if snap_dst.exists():
                    shutil.rmtree(snap_dst)
                shutil.copytree(snap_src, snap_dst)
        selected.append(str(cid))

    return {
        "source_set": source_set,
        "dest_set": dest_set,
        "selected_count": len(selected),
        "selected": selected,
        "tier_distribution": _count_by_tier(keep),
        "difficulty_distribution": _count_by_band(keep),
        "difficulty_quota": difficulty_report,
        "missing": sorted(wanted - set(selected)),
        "dry_run": dry_run,
    }


def _count_by_band(cases: list[dict[str, Any]]) -> dict[str, int]:
    out: dict[str, int] = {}
    for c in cases:
        b = difficulty_band(c.get("p_hat"))
        out[b] = out.get(b, 0) + 1
    return dict(sorted(out.items()))


def render_calibration_md(report: dict[str, Any]) -> str:
    incomplete = int(report.get("incomplete_panel_count") or 0)
    dropped = report.get("rows_dropped_by_anchor") or {}
    lines = [
        "# Case Calibration",
        "",
        f"- Case set: `{report.get('case_set')}`",
        f"- Anchors: {', '.join(report.get('anchors') or [])}",
        f"- Runs: {report.get('run_count')} (repeats={report.get('repeats')})",
        f"- Kept: **{report.get('kept_count')}** / {report.get('total_cases')}",
    ]
    if incomplete or dropped:
        # Kept apart from the quality verdicts on purpose. A reader who sees only "Kept: 4 / 31"
        # concludes the set is bad, when what happened is that the sweep did not finish.
        lines.append(
            f"- **未测完：{incomplete} 条**（面板缺锚点，判 `{INCOMPLETE_PANEL}`）"
            " —— 这些不是坏题，对策是重跑"
        )
        if dropped:
            detail = ", ".join(f"{name} {n}" for name, n in dropped.items())
            lines.append(f"- 因 infra 错误丢弃的行：{detail}")
    lines += [
        "",
        "## 通过率分布",
        "",
        "| p_hat 区间 | 全部 | 保留 |",
        "| --- | ---: | ---: |",
    ]
    all_b = report.get("p_hat_distribution") or {}
    kept_b = report.get("kept_p_hat_distribution") or {}
    for bucket in ("0.0-0.2", "0.2-0.5", "0.5-0.8", "0.8-1.0"):
        lines.append(f"| {bucket} | {all_b.get(bucket, 0)} | {kept_b.get(bucket, 0)} |")
    lines.extend(
        [
            "",
            "## 保留用例分层",
            "",
            "| tier | 数量 |",
            "| --- | ---: |",
        ]
    )
    for tier, n in (report.get("tier_distribution") or {}).items():
        lines.append(f"| {tier} | {n} |")
    lines.extend(
        [
            "",
            "## Case 明细",
            "",
            "| case_id | tier | p_hat | 区分度 spread | r_pb | flaky | keep | 原因 |",
            "| --- | --- | ---: | ---: | ---: | --- | --- | --- |",
        ]
    )
    for c in report.get("cases") or []:
        rpb = c.get("point_biserial")
        lines.append(
            f"| {c.get('case_id')} | {c.get('tier') or '-'} | {float(c.get('p_hat') or 0):.2f} "
            f"| {float(c.get('spread') or 0):.2f} | {f'{rpb:.2f}' if rpb is not None else '-'} "
            f"| {c.get('flaky')} | {c.get('keep')} | {'; '.join(c.get('reasons') or [])} |"
        )
    lines.append("")
    return "\n".join(lines)
