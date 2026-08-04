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
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from aibench.cases import case_set_dir, is_case_json_path
from aibench.io_util import load_json, load_yaml, repo_root, write_json
from aibench.stats import point_biserial, wilson_ci

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
    spread: float = 0.0
    point_biserial: float | None = None
    flaky: bool = False
    keep: bool = True
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def anchor_fingerprint(anchors: list[AnchorSpec]) -> str:
    """Identity of the panel a calibration was measured against.

    Includes the *contents* of each referenced config, not just its path: swapping the model
    inside `glm52.yaml` changes what the anchors mean while every path stays the same, and a
    p_hat measured against the old panel would silently keep being trusted.
    """
    root = repo_root()
    parts: list[str] = []
    for a in sorted(anchors, key=lambda x: x.name):
        parts.append(a.name)
        for rel in (a.agent_config, a.model_config, a.run_config):
            path = _abs(root, rel)
            parts.append(path.read_text(encoding="utf-8") if path and path.is_file() else str(rel))
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()[:16]


def plan_calibration(
    case_ids: list[str],
    fingerprints: dict[str, str],
    previous: dict[str, Any] | None,
    *,
    panel: str,
) -> tuple[list[str], list[dict[str, Any]]]:
    """Split cases into those needing a run and those whose earlier result still holds.

    A previous result is reusable only when both the case and the panel are byte-identical to
    what produced it; either changing invalidates the measurement.
    """
    if not previous or previous.get("anchor_fingerprint") != panel:
        return list(case_ids), []
    prior = {
        c["case_id"]: c
        for c in previous.get("cases") or []
        if c.get("fingerprint") and c["case_id"] in fingerprints
    }
    reusable = [
        prior[cid]
        for cid in case_ids
        if cid in prior and prior[cid].get("fingerprint") == fingerprints.get(cid)
    ]
    reused_ids = {c["case_id"] for c in reusable}
    return [cid for cid in case_ids if cid not in reused_ids], reusable


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
            outcomes.setdefault(cid, {}).setdefault(i, []).append(bool(row.get("passed")))

    run_indices = list(range(len(runs)))
    runs_of_anchor: dict[str, list[int]] = {}
    for i, name in enumerate(anchor_of):
        runs_of_anchor.setdefault(name, []).append(i)

    # Ability score per run = how many cases it solved; the baseline r_pb is measured against.
    totals = [
        float(sum(1 for per_run in outcomes.values() for ok in per_run.get(i, []) if ok))
        for i in run_indices
    ]

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
        flaky = False
        for name, indices in runs_of_anchor.items():
            hits = [ok for i in indices for ok in per_run.get(i, [])]
            if not hits:
                continue
            rate = sum(1 for ok in hits if ok) / len(hits)
            by_anchor[name] = rate
            flaky = flaky or 0.0 < rate < 1.0
        spread = (max(by_anchor.values()) - min(by_anchor.values())) if by_anchor else 0.0

        item = [
            (sum(1 for ok in per_run[i] if ok) / len(per_run[i])) if per_run.get(i) else 0.0
            for i in run_indices
        ]
        r_pb = point_biserial(item, totals)

        reasons: list[str] = []
        if p_hat > pol.p_max:
            reasons.append(f"too_easy(p={p_hat:.2f}>{pol.p_max})")
        if p_hat < pol.p_min:
            reasons.append(f"unsolved_by_all(p={p_hat:.2f}<{pol.p_min}) — verify the case itself")
        if r_pb is not None and r_pb < pol.min_rpb and pol.p_min <= p_hat <= pol.p_max:
            reasons.append(f"no_discrimination(r_pb={r_pb:.2f}<{pol.min_rpb})")

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
        "cases": [r.to_dict() for r in reports],
    }


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
    from aibench.validity import case_fingerprint

    cases = load_cases(case_set, validate=True)
    fingerprints = {c.case_id: c.metadata.get("fingerprint") or case_fingerprint(c) for c in cases}
    previous = load_json(reuse_from) if reuse_from and reuse_from.is_file() else None
    todo, reused = plan_calibration([c.case_id for c in cases], fingerprints, previous, panel=panel)
    if reused:
        print(f"reusing {len(reused)} unchanged case results; running {len(todo)}")

    run_set = case_set
    if reused and todo:
        run_set = _materialize_subset(case_set, todo)
    elif reused and not todo:
        run_set = None  # nothing changed; the previous numbers still stand

    runs: list[dict[str, Any]] = []
    if run_set is not None:
        for anchor in anchors:
            for rep in range(1, repeats + 1):
                run_dir = run_benchmark(
                    run_config_path=_abs(root, anchor.run_config),
                    agent_config_path=_abs(root, anchor.agent_config),
                    model_config_path=_abs(root, anchor.model_config),
                    case_set=run_set,
                    run_id=f"cal-{anchor.name}-r{rep}",
                    output_root=cal_dir,
                    case_workers=case_workers,
                )
                runs.append(
                    {
                        "anchor": anchor.name,
                        "repeat": rep,
                        "rows": read_result_rows(run_dir / "results.jsonl"),
                    }
                )

    report = aggregate_calibration(runs, policy=policy)
    if reused:
        report = _merge_reused(report, reused, policy=policy)
    report["case_set"] = case_set
    report["repeats"] = repeats
    report["anchor_fingerprint"] = panel
    report["reused_case_count"] = len(reused)
    report["recalibrated_case_count"] = len(todo)
    write_json(cal_dir / "calibration.json", report)
    (cal_dir / "calibration_report.md").write_text(render_calibration_md(report), encoding="utf-8")
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
) -> dict[str, Any]:
    """Fold previously measured cases back in and recompute the set-level distributions."""
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
    dry_run: bool = False,
) -> dict[str, Any]:
    """Copy the cases calibration kept into a new case set, best discriminators first."""
    keep = [c for c in calibration.get("cases") or [] if c.get("keep")]
    keep.sort(key=_rank)
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
        "missing": sorted(wanted - set(selected)),
        "dry_run": dry_run,
    }


def render_calibration_md(report: dict[str, Any]) -> str:
    lines = [
        "# Case Calibration",
        "",
        f"- Case set: `{report.get('case_set')}`",
        f"- Anchors: {', '.join(report.get('anchors') or [])}",
        f"- Runs: {report.get('run_count')} (repeats={report.get('repeats')})",
        f"- Kept: **{report.get('kept_count')}** / {report.get('total_cases')}",
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
