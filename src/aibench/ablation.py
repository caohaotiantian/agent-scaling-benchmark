"""Run multi-config ablation matrix and aggregate overview tables."""

from __future__ import annotations

import shutil
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from aibench.cases import case_set_dir
from aibench.io_util import load_json, load_yaml, repo_root, write_json
from aibench.report import render_summary_tables_json
from aibench.runner import run_benchmark


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
    for p in sorted(src.glob("*.json")):
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
            "experiment_name": exp,
            "run_id": summary.get("run_id"),
            "run_dir": str(run_dir),
            "algorithm_name": summary.get("algorithm_name"),
            "agent_name": summary.get("agent_name"),
            "main_model": summary.get("main_model"),
            "success_rate": summary.get("success_rate"),
            "success_count": summary.get("success_count"),
            "case_count": summary.get("case_count"),
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

    write_json(
        abl_dir / "ablation_summary.json",
        {
            "matrix": str(matrix_path),
            "skip_weak_grader": skip_weak,
            "baseline_experiment": base_name,
            "parallel": parallel,
            "runs": rows,
        },
    )
    report = _render_ablation_report(rows, baseline=base_name)
    (abl_dir / "ablation_report.md").write_text(report, encoding="utf-8")
    return abl_dir


def _render_ablation_report(rows: list[dict[str, Any]], *, baseline: str | None) -> str:
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
            "| experiment | run_id | success_rate | tokens | cost | run_dir |",
            "| --- | --- | ---: | ---: | ---: | --- |",
        ]
    )
    for r in rows:
        lines.append(
            f"| {r['experiment_name']} | {r['run_id']} | {float(r['success_rate'] or 0):.3f} "
            f"| {r['total_tokens']} | {r.get('total_cost')} | {r['run_dir']} |"
        )
    lines.append("")
    return "\n".join(lines)
