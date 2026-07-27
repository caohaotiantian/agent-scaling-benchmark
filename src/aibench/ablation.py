"""Run multi-config ablation matrix and aggregate overview tables."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from aibench.io_util import load_yaml, repo_root, write_json
from aibench.report import render_summary_tables_json
from aibench.runner import run_benchmark


def load_matrix(path: Path) -> dict[str, Any]:
    data = load_yaml(path)
    if "runs" not in data or not isinstance(data["runs"], list) or not data["runs"]:
        raise ValueError("matrix YAML must contain non-empty runs: list")
    return data


def run_ablation(
    matrix_path: Path,
    *,
    output_root: Path | None = None,
    case_set_override: str | None = None,
) -> Path:
    root = repo_root()
    matrix = load_matrix(matrix_path)
    case_set = case_set_override or matrix.get("case_set") or "seed-v0"
    out_root = output_root or (root / "runs")
    ts = time.strftime("%Y%m%d_%H%M%S")
    abl_dir = out_root / f"ablation_{ts}"
    abl_dir.mkdir(parents=True, exist_ok=True)

    rows: list[dict[str, Any]] = []
    for i, item in enumerate(matrix["runs"]):
        exp = item.get("experiment_name") or f"run-{i}"
        agent = item.get("agent_config") or "configs/agents/mock.yaml"
        model = item.get("model_config") or "configs/models/mock-model.yaml"
        run_cfg = item.get("run_config")
        run_id = item.get("run_id") or f"ablation-{exp}"

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
            case_set=item.get("case_set") or case_set,
            run_id=run_id,
            output_root=abl_dir,
        )
        summary = __import__("aibench.io_util", fromlist=["load_json"]).load_json(
            run_dir / "summary.json"
        )
        # overlay algorithm name if provided
        if item.get("algorithm_name"):
            summary["algorithm_name"] = item["algorithm_name"]
        tables = render_summary_tables_json(summary)
        rows.append(
            {
                "experiment_name": exp,
                "run_id": summary.get("run_id"),
                "run_dir": str(run_dir),
                "algorithm_name": summary.get("algorithm_name"),
                "agent_name": summary.get("agent_name"),
                "main_model": summary.get("main_model"),
                "success_rate": summary.get("success_rate"),
                "case_count": summary.get("case_count"),
                "total_tokens": summary.get("total_tokens"),
                "total_wall_time_h": summary.get("total_wall_time_h"),
                "overview_row": tables["overview_row"],
                "general_row": tables["general_row"],
            }
        )

    write_json(abl_dir / "ablation_summary.json", {"matrix": str(matrix_path), "runs": rows})
    report = _render_ablation_report(rows)
    (abl_dir / "ablation_report.md").write_text(report, encoding="utf-8")
    return abl_dir


def _render_ablation_report(rows: list[dict[str, Any]]) -> str:
    lines = [
        "# Ablation Report",
        "",
        "## 项目效果综述表",
        "",
        "| 算法名称 | Agent与模型 | 基础/主模型 | Benchmark | Case数 | 主指标名称 | 主指标值 | 总体耗时(h) | 总体Token消耗 | 相对基线收益 |",
        "| --- | --- | --- | --- | ---: | --- | --- | ---: | ---: | --- |",
    ]
    for r in rows:
        o = r["overview_row"]
        sr = float(r.get("success_rate") or 0) * 100
        lines.append(
            f"| {o.get('算法名称')} | {o.get('Agent与模型')} | {o.get('基础/主模型')} "
            f"| {o.get('Benchmark')} | {o.get('Case数')} | {o.get('主指标名称')} "
            f"| {sr:.1f}% | {float(o.get('总体耗时(h)') or 0):.6f} "
            f"| {o.get('总体Token消耗')} |  |"
        )
    lines.extend(
        [
            "",
            "## Runs",
            "",
            "| experiment | run_id | success_rate | tokens | run_dir |",
            "| --- | --- | ---: | ---: | --- |",
        ]
    )
    for r in rows:
        lines.append(
            f"| {r['experiment_name']} | {r['run_id']} | {float(r['success_rate'] or 0):.3f} "
            f"| {r['total_tokens']} | {r['run_dir']} |"
        )
    lines.append("")
    return "\n".join(lines)
