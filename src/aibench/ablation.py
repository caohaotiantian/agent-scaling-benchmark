"""Run multi-config ablation matrix and aggregate overview tables."""

from __future__ import annotations

import shutil
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from aibench.calibrate import read_result_rows
from aibench.cases import case_set_dir
from aibench.io_util import load_json, load_yaml, repo_root, write_json
from aibench.report import render_summary_tables_json
from aibench.runner import run_benchmark
from aibench.stats import mcnemar_test, paired_outcomes


def load_matrix(path: Path) -> dict[str, Any]:
    data = load_yaml(path)
    if "runs" not in data or not isinstance(data["runs"], list) or not data["runs"]:
        raise ValueError("matrix YAML must contain non-empty runs: list")
    return data


def _filter_weak_grader_case_set(case_set: str, *, skip_weak: bool) -> str:
    """If skip_weak, materialize a temp case set without weak_grader=true cases."""
    if not skip_weak:
        return case_set
    src = case_set_dir(case_set)
    if not src.is_dir():
        return case_set
    weak_count = 0
    strong: list[Path] = []
    from aibench.cases import is_case_json_path

    for p in sorted(src.glob("*.json")):
        if not is_case_json_path(p):
            continue
        raw = load_json(p)
        if (raw.get("metadata") or {}).get("weak_grader"):
            weak_count += 1
            continue
        strong.append(p)
    if weak_count == 0:
        return case_set
    # write filtered set under benchmarks/ai_coding/cases/.ablation-filtered-<set>
    dest_name = f".ablation-filtered-{case_set}"
    dest = repo_root() / "benchmarks/ai_coding/cases" / dest_name
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True, exist_ok=True)
    for p in strong:
        shutil.copy2(p, dest / p.name)
    # copy snapshots if any
    snap = src / "snapshots"
    if snap.is_dir():
        shutil.copytree(snap, dest / "snapshots")
    if not strong:
        raise ValueError(f"case set {case_set!r} has only weak_grader cases; nothing to ablate")
    return dest_name


def run_ablation(
    matrix_path: Path,
    *,
    output_root: Path | None = None,
    case_set_override: str | None = None,
    skip_weak_grader: bool = True,
    allow_weak_grader: bool = False,
    parallel: int = 1,
    baseline_experiment: str | None = None,
) -> Path:
    root = repo_root()
    matrix = load_matrix(matrix_path)
    case_set = case_set_override or matrix.get("case_set") or "auto-v0"
    skip_weak = (
        skip_weak_grader and not allow_weak_grader and not matrix.get("allow_weak_grader", False)
    )
    # Per-row case sets may differ; filter default set once
    filtered_default = _filter_weak_grader_case_set(case_set, skip_weak=skip_weak)

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
        row_case = item.get("case_set") or case_set
        if row_case == case_set:
            use_set = filtered_default
        else:
            use_set = _filter_weak_grader_case_set(row_case, skip_weak=skip_weak)

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

    rows: list[dict[str, Any]] = []
    parallel = max(1, int(parallel or matrix.get("parallel") or 1))
    if parallel == 1:
        for job in jobs:
            rows.append(_one(job))
    else:
        with ThreadPoolExecutor(max_workers=parallel) as ex:
            futs = [ex.submit(_one, job) for job in jobs]
            for fut in as_completed(futs):
                rows.append(fut.result())
        rows.sort(key=lambda r: r.get("index", 0))

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

    pairwise = compare_runs_pairwise(rows, baseline=base_name)
    tier_matrix = {r["experiment_name"]: r.get("stratified_by_tier") or {} for r in rows}

    # Per-case rows are only needed to build the comparisons; keep them out of the summary file.
    slim_rows = [{k: v for k, v in r.items() if k != "case_rows"} for r in rows]
    write_json(
        abl_dir / "ablation_summary.json",
        {
            "matrix": str(matrix_path),
            "skip_weak_grader": skip_weak,
            "baseline_experiment": base_name,
            "parallel": parallel,
            "runs": slim_rows,
            "pairwise_comparisons": pairwise,
            "tier_matrix": tier_matrix,
        },
    )
    report = _render_ablation_report(
        slim_rows, baseline=base_name, pairwise=pairwise, tier_matrix=tier_matrix
    )
    (abl_dir / "ablation_report.md").write_text(report, encoding="utf-8")
    return abl_dir


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


def _render_ablation_report(
    rows: list[dict[str, Any]],
    *,
    baseline: str | None,
    pairwise: list[dict[str, Any]] | None = None,
    tier_matrix: dict[str, dict[str, Any]] | None = None,
) -> str:
    lines = [
        "# Ablation Report",
        "",
        f"- Baseline experiment: `{baseline}`",
        "",
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
            f"| {sr:.1f}% | {float(o.get('总体耗时(h)') or 0):.6f} "
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

    # A run whose cases all failed to execute reports success_rate 0.0, which reads as "the
    # agent could not solve anything" when it actually means the harness never ran it.
    broken = [r for r in rows if not r.get("effective_case_count")]
    if broken:
        lines.extend(["", "> **警告**：以下实验没有任何有效 case（全部 infra_error），"])
        lines.append("> 其 0% 成功率与相对基线收益**不是能力结论**，请先排查环境/适配器：")
        for r in broken:
            lines.append(
                f"> - `{r['experiment_name']}`：{r.get('infra_error_count')} 个基础设施失败"
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
