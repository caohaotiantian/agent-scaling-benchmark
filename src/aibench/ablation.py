"""Run multi-config ablation matrix and aggregate overview tables."""

from __future__ import annotations

import shutil
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from aibench.calibrate import read_result_rows
from aibench.cases import case_set_dir
from aibench.io_util import load_json, load_yaml, repo_root, write_json, write_text
from aibench.report import format_hours, format_pct, render_summary_tables_json
from aibench.runner import run_benchmark
from aibench.stats import mcnemar_test, paired_outcomes


def load_matrix(path: Path) -> dict[str, Any]:
    data = load_yaml(path)
    if "runs" not in data or not isinstance(data["runs"], list) or not data["runs"]:
        raise ValueError("matrix YAML must contain non-empty runs: list")
    return data


def _filter_unusable_cases(
    case_set: str,
    *,
    skip_weak: bool,
    skip_invalid: bool,
) -> tuple[str, dict[str, int]]:
    """Materialize a temp case set without the cases that must not be measured.

    Two exclusions, both of which used to be missing on the run path.

    ``weak_grader`` was already handled. ``validity_ok: false`` was not: `audit-cases` writes
    the verdict back into the case's own metadata and then nothing anywhere consulted it, so a
    case whose stub passes its own grader, or whose hidden test demands an unknowable name,
    sat in the denominator of every ablation. On disk when this was written: **64 of 133
    `_rev2026` cases and 9 of 26 `_retryout` cases carry `validity_ok: false`**.

    A case that was never audited has no `validity_ok` key at all. That is not a failed audit
    and is left in — the alternative would silently empty every set that predates the gate.
    """
    src = case_set_dir(case_set)
    counts = {"weak_grader": 0, "validity_failed": 0}
    if not (skip_weak or skip_invalid) or not src.is_dir():
        return case_set, counts

    from aibench.cases import is_case_json_path

    keep: list[Path] = []
    for p in sorted(src.glob("*.json")):
        if not is_case_json_path(p):
            continue
        meta = load_json(p).get("metadata") or {}
        if skip_weak and meta.get("weak_grader"):
            counts["weak_grader"] += 1
            continue
        if skip_invalid and meta.get("validity_ok") is False:
            counts["validity_failed"] += 1
            continue
        keep.append(p)
    if not any(counts.values()):
        return case_set, counts
    if not keep:
        raise ValueError(
            f"case set {case_set!r} has nothing left to ablate: "
            f"{counts['weak_grader']} weak_grader, {counts['validity_failed']} validity_ok=false"
        )
    # write filtered set under benchmarks/ai_coding/cases/.ablation-filtered-<set>
    dest_name = f".ablation-filtered-{case_set}"
    dest = repo_root() / "benchmarks/ai_coding/cases" / dest_name
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True, exist_ok=True)
    for p in keep:
        shutil.copy2(p, dest / p.name)
    # copy snapshots if any
    snap = src / "snapshots"
    if snap.is_dir():
        shutil.copytree(snap, dest / "snapshots")
    return dest_name, counts


def run_ablation(
    matrix_path: Path,
    *,
    output_root: Path | None = None,
    case_set_override: str | None = None,
    skip_weak_grader: bool = True,
    allow_weak_grader: bool = False,
    allow_invalid_cases: bool = False,
    parallel: int | None = None,
    baseline_experiment: str | None = None,
) -> Path:
    root = repo_root()
    matrix = load_matrix(matrix_path)
    case_set = case_set_override or matrix.get("case_set") or "auto-v0"
    skip_weak = (
        skip_weak_grader and not allow_weak_grader and not matrix.get("allow_weak_grader", False)
    )
    skip_invalid = not allow_invalid_cases and not matrix.get("allow_invalid_cases", False)
    # Every distinct case set is filtered up front, never inside a worker. Filtering rmtree's
    # and repopulates a shared `.ablation-filtered-<set>` directory, so two rows filtering the
    # same set concurrently would let one of them run against a half-copied case set — a wrong
    # result, not a crash.
    filtered: dict[str, str] = {}
    excluded: dict[str, dict[str, int]] = {}
    for row_case in {item.get("case_set") or case_set for item in matrix["runs"]} | {case_set}:
        filtered[row_case], counts = _filter_unusable_cases(
            row_case, skip_weak=skip_weak, skip_invalid=skip_invalid
        )
        if any(counts.values()):
            excluded[row_case] = counts
            print(
                f"[filter] {row_case}: excluded {counts['weak_grader']} weak_grader and "
                f"{counts['validity_failed']} validity_ok=false case(s)"
            )

    out_root = output_root or (root / "runs")
    ts = time.strftime("%Y%m%d_%H%M%S")
    abl_dir = out_root / f"ablation_{ts}"
    abl_dir.mkdir(parents=True, exist_ok=True)

    jobs: list[dict[str, Any]] = []
    for i, item in enumerate(matrix["runs"]):
        jobs.append({"index": i, "item": item})

    def _one(job: dict[str, Any]) -> dict[str, Any]:
        item = job["item"]
        i = job["index"]
        exp = item.get("experiment_name") or f"run-{i}"
        agent = item.get("agent_config") or "configs/agents/openai_compat.yaml"
        model = item.get("model_config") or "configs/models/glm52.yaml"
        run_cfg = item.get("run_config")
        run_id = item.get("run_id") or f"ablation-{exp}"
        use_set = filtered[item.get("case_set") or case_set]

        agent_path = Path(agent)
        model_path = Path(model)
        if not agent_path.is_absolute():
            agent_path = root / agent_path
        if not model_path.is_absolute():
            model_path = root / model_path
        run_cfg_path = Path(run_cfg) if run_cfg else None
        if run_cfg_path and not run_cfg_path.is_absolute():
            run_cfg_path = root / run_cfg_path

        run_dir = run_benchmark(
            run_config_path=run_cfg_path,
            agent_config_path=agent_path,
            model_config_path=model_path,
            case_set=use_set,
            run_id=run_id,
            output_root=abl_dir,
        )
        summary = load_json(run_dir / "summary.json")
        if item.get("algorithm_name"):
            summary["algorithm_name"] = item["algorithm_name"]
        tables = render_summary_tables_json(summary)
        return {
            "case_rows": read_result_rows(run_dir / "results.jsonl"),
            "stratified_by_tier": summary.get("stratified_by_tier"),
            "experiment_name": exp,
            "run_id": summary.get("run_id"),
            "run_dir": str(run_dir),
            "algorithm_name": summary.get("algorithm_name"),
            "agent_name": summary.get("agent_name"),
            "main_model": summary.get("main_model"),
            "success_rate": summary.get("success_rate"),
            "success_count": summary.get("success_count"),
            "case_count": summary.get("case_count"),
            "pass_at_1": summary.get("pass_at_1"),
            "pass_at_k": summary.get("pass_at_k"),
            "attempts_per_case": summary.get("attempts_per_case"),
            "cost_curve": summary.get("cost_curve"),
            "effective_case_count": summary.get("effective_case_count"),
            "infra_error_count": summary.get("infra_error_count"),
            "infra_error_rate": summary.get("infra_error_rate"),
            "total_tokens": summary.get("total_tokens"),
            "total_cost": summary.get("total_cost"),
            "total_wall_time_h": summary.get("total_wall_time_h"),
            "overview_row": tables["overview_row"],
            "general_row": tables["general_row"],
            "failure_diagnostics": summary.get("failure_diagnostics"),
            "index": i,
        }

    def _one_guarded(job: dict[str, Any]) -> dict[str, Any]:
        """One matrix row, surviving its own failure.

        A matrix is hours of paid work. Letting a bad gateway on row 3 discard rows 1 and 2 —
        which is what an uncaught exception did — is the most expensive failure mode here.
        """
        exp = job["item"].get("experiment_name") or f"run-{job['index']}"
        try:
            return _one(job)
        except Exception as e:
            print(f"[error] experiment {exp!r} failed: {e}")
            return {
                "experiment_name": exp,
                "index": job["index"],
                "run_id": None,
                "run_dir": None,
                "failed": True,
                "error": str(e),
                "success_rate": None,
                "total_tokens": 0,
                "overview_row": {},
                "general_row": {},
            }

    rows: list[dict[str, Any]] = []
    # `parallel or matrix.get("parallel")` short-circuited on the first operand every time,
    # because the CLI passed a truthy default of 1 unconditionally — so 11 of the 12 shipped
    # matrices declared `parallel: 3` and every one of them ran serially while
    # `ablation_summary.json` recorded `"parallel": 1`. The explicit `is None` is what lets the
    # matrix speak when the caller did not.
    parallel = max(1, int(parallel if parallel is not None else matrix.get("parallel") or 1))
    if parallel == 1:
        rows = [_one_guarded(job) for job in jobs]
    else:
        with ThreadPoolExecutor(max_workers=parallel) as ex:
            futs = [ex.submit(_one_guarded, job) for job in jobs]
            rows = [fut.result() for fut in as_completed(futs)]
    rows.sort(key=lambda r: r.get("index", 0))

    failed_rows = [r for r in rows if r.get("failed")]
    rows = [r for r in rows if not r.get("failed")]
    if not rows:
        raise RuntimeError(
            "every ablation experiment failed: "
            + "; ".join(f"{r['experiment_name']}: {r['error']}" for r in failed_rows)
        )

    # baseline relative lift
    base_name = (
        baseline_experiment
        or matrix.get("baseline_experiment")
        or (rows[0]["experiment_name"] if rows else None)
    )
    base_row = next((r for r in rows if r["experiment_name"] == base_name), None)
    base_sr = float(base_row["success_rate"]) if base_row else None
    for r in rows:
        if base_sr is None:
            r["relative_success_lift"] = None
            r["overview_row"]["相对基线收益"] = None
        else:
            lift = float(r["success_rate"] or 0) - base_sr
            r["relative_success_lift"] = lift
            r["overview_row"]["相对基线收益"] = f"{lift * 100:+.1f}pp"

    attach_token_amplification(rows, baseline=base_name)
    pairwise = compare_runs_pairwise(rows, baseline=base_name)
    tier_matrix = {r["experiment_name"]: r.get("stratified_by_tier") or {} for r in rows}

    # Per-case rows are only needed to build the comparisons; keep them out of the summary file.
    slim_rows = [{k: v for k, v in r.items() if k != "case_rows"} for r in rows]
    write_json(
        abl_dir / "ablation_summary.json",
        {
            "matrix": str(matrix_path),
            "skip_weak_grader": skip_weak,
            "skip_invalid_cases": skip_invalid,
            "excluded_cases": excluded,
            "baseline_experiment": base_name,
            "parallel": parallel,
            "runs": slim_rows,
            "failed_runs": failed_rows,
            "pairwise_comparisons": pairwise,
            "tier_matrix": tier_matrix,
        },
    )
    report = _render_ablation_report(
        slim_rows,
        excluded=excluded,
        baseline=base_name,
        pairwise=pairwise,
        tier_matrix=tier_matrix,
        failed=failed_rows,
    )
    write_text(abl_dir / "ablation_report.md", report)
    return abl_dir


def attach_token_amplification(rows: list[dict[str, Any]], *, baseline: str | None) -> None:
    """Record each run's token spend as a multiple of the baseline's.

    An accuracy gain bought with 5x the tokens is a different result from the same gain at
    equal cost, and the overview table's absolute token column does not make that comparison
    for the reader.
    """
    base = next((r for r in rows if r["experiment_name"] == baseline), None)
    base_tokens = int((base or {}).get("total_tokens") or 0)
    for r in rows:
        r["token_amplification"] = (
            (int(r.get("total_tokens") or 0) / base_tokens) if base_tokens else None
        )


def compare_runs_pairwise(
    rows: list[dict[str, Any]],
    *,
    baseline: str | None,
) -> list[dict[str, Any]]:
    """McNemar every non-baseline run against the baseline on the cases they share.

    Two runs measured on the same case set are paired data. Comparing their independent Wilson
    intervals throws that pairing away and calls real differences inconclusive; the paired test
    keeps it.
    """
    base = next((r for r in rows if r["experiment_name"] == baseline), None)
    if base is None:
        return []
    base_rows = base.get("case_rows") or []
    out: list[dict[str, Any]] = []
    for r in rows:
        if r["experiment_name"] == baseline:
            continue
        both, only_b, only_a, neither = paired_outcomes(base_rows, r.get("case_rows") or [])
        test = mcnemar_test(only_b, only_a)
        out.append(
            {
                "baseline": baseline,
                "candidate": r["experiment_name"],
                "both_passed": both,
                "only_baseline": only_b,
                "only_candidate": only_a,
                "neither": neither,
                **test,
            }
        )
    return out


#: Below this share of a run's cases actually executing, the reported rate says more about the
#: gateway than about the model. Not a hard rule — it decides whether a warning prints, never
#: whether a number is used.
_EFFECTIVE_SHARE_FLOOR = 0.8


def _infra_dominated(row: dict[str, Any]) -> bool:
    total = int(row.get("case_count") or 0)
    effective = int(row.get("effective_case_count") or 0)
    if not total:
        return True
    return effective < _EFFECTIVE_SHARE_FLOOR * total


def _render_ablation_report(
    rows: list[dict[str, Any]],
    *,
    baseline: str | None,
    pairwise: list[dict[str, Any]] | None = None,
    tier_matrix: dict[str, dict[str, Any]] | None = None,
    failed: list[dict[str, Any]] | None = None,
    excluded: dict[str, dict[str, int]] | None = None,
) -> str:
    lines = [
        "# Ablation Report",
        "",
        f"- Baseline experiment: `{baseline}`",
        "",
        *(
            [
                "> **已排除的 case**（不进入任何分母）——",
                *[
                    f"> - `{cs}`：{c['weak_grader']} 条 weak_grader，"
                    f"{c['validity_failed']} 条 `validity_ok: false`"
                    for cs, c in sorted((excluded or {}).items())
                ],
                "",
            ]
            if excluded
            else []
        ),
        *(
            [
                "> **警告**：以下实验执行失败，未计入下表——",
                *[f"> - `{r['experiment_name']}`：{r.get('error')}" for r in failed],
                "",
            ]
            if failed
            else []
        ),
        "## 项目效果综述表",
        "",
        "| 算法名称 | Agent与模型 | 基础/主模型 | Benchmark | Case数 | 主指标名称 | 主指标值 | 总体耗时(h) | 总体Token消耗 | 相对基线收益 |",
        "| --- | --- | --- | --- | ---: | --- | --- | ---: | ---: | --- |",
    ]
    for r in rows:
        o = r["overview_row"]
        sr = float(r.get("success_rate") or 0) * 100
        lift = o.get("相对基线收益")
        if lift is None and r.get("relative_success_lift") is not None:
            lift = f"{float(r['relative_success_lift']) * 100:+.1f}pp"
        lines.append(
            f"| {o.get('算法名称')} | {o.get('Agent与模型')} | {o.get('基础/主模型')} "
            f"| {o.get('Benchmark')} | {o.get('Case数')} | {o.get('主指标名称')} "
            f"| {sr:.1f}% | {format_hours(o.get('总体耗时(h)'))} "
            f"| {o.get('总体Token消耗')} | {lift if lift is not None else ''} |"
        )
    lines.extend(
        [
            "",
            "## Runs",
            "",
            "| experiment | run_id | success_rate | 有效Case | 基础设施失败 | tokens | cost | run_dir |",
            "| --- | --- | ---: | ---: | ---: | ---: | ---: | --- |",
        ]
    )
    for r in rows:
        lines.append(
            f"| {r['experiment_name']} | {r['run_id']} | {float(r['success_rate'] or 0):.3f} "
            f"| {r.get('effective_case_count')} | {r.get('infra_error_count')} "
            f"| {r['total_tokens']} | {r.get('total_cost')} | {r['run_dir']} |"
        )

    # A run whose cases mostly failed to execute reports a rate computed on whatever survived,
    # which reads as "the agent could not solve much" when it actually means the harness never
    # ran it. The guard used to be `if not effective_case_count`, firing only at *exactly*
    # zero — so 1 effective case out of 167 got a full-weight rate and no warning at all.
    broken = [r for r in rows if _infra_dominated(r)]
    if broken:
        lines.extend(
            ["", "> **警告**：以下实验的有效 case 数远低于 case 总数（基础设施失败为主），"]
        )
        lines.append("> 其成功率与相对基线收益**不是能力结论**，请先排查环境/适配器：")
        for r in broken:
            eff = r.get("effective_case_count") or 0
            lines.append(
                f"> - `{r['experiment_name']}`：有效 {eff}/{r.get('case_count')} 条，"
                f"{r.get('infra_error_count')} 个基础设施失败"
            )

    if tier_matrix:
        tiers = sorted({t for strata in tier_matrix.values() for t in strata})
        if tiers:
            lines.extend(["", "## 分层成功率（按 tier）", ""])
            lines.append("| experiment | " + " | ".join(tiers) + " |")
            lines.append("| --- | " + " | ".join("---:" for _ in tiers) + " |")
            for exp, strata in tier_matrix.items():
                cells = []
                for t in tiers:
                    st = strata.get(t)
                    cells.append(
                        f"{float(st.get('success_rate') or 0) * 100:.0f}% (n={st.get('n')})"
                        if st
                        else "-"
                    )
                lines.append(f"| {exp} | " + " | ".join(cells) + " |")

    if any(r.get("token_amplification") is not None for r in rows):
        lines.extend(
            [
                "",
                "## 采样扩展与成本",
                "",
                "`pass@k − pass@1` 是重复采样暴露出的上限空间；token 倍数是买到它的代价。",
                "两列要一起读：准确率相同而 token 少的组合更强。",
                "",
                "| experiment | 采样次数/case | pass@1 | pass@k | 成功率 | token | 相对基线 token |",
                "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
            ]
        )
        for r in rows:
            amp = r.get("token_amplification")
            lines.append(
                f"| {r['experiment_name']} | {r.get('attempts_per_case') or 1} "
                f"| {format_pct(r.get('pass_at_1'))} | {format_pct(r.get('pass_at_k'))} "
                f"| {format_pct(r.get('success_rate'))} | {r.get('total_tokens')} "
                f"| {'-' if amp is None else f'{amp:.2f}x'} |"
            )

    if pairwise:
        lines.extend(
            [
                "",
                "## 配对显著性检验（McNemar，相对基线）",
                "",
                "同一 case 集上的配对比较；`b`=仅基线通过，`c`=仅候选通过。p<0.05 视为能力水平显著不同。",
                "",
                "| 候选 | 均通过 | 仅基线(b) | 仅候选(c) | 不一致数 | p 值 | 显著 |",
                "| --- | ---: | ---: | ---: | ---: | ---: | --- |",
            ]
        )
        for p in pairwise:
            lines.append(
                f"| {p['candidate']} | {p['both_passed']} | {p['only_baseline']} "
                f"| {p['only_candidate']} | {p['discordant']} | {p['p_value']:.4f} "
                f"| {'是' if p['significant'] else '否'} |"
            )
    lines.append("")
    return "\n".join(lines)
