"""Export ablation/run tables to CSV and optional Excel."""

from __future__ import annotations

import csv
from pathlib import Path

from aibench.io_util import atomic_write, load_json


def export_ablation_csv(ablation_dir: Path, out_csv: Path | None = None) -> Path:
    summary = load_json(ablation_dir / "ablation_summary.json")
    rows = summary.get("runs") or []
    out = out_csv or (ablation_dir / "ablation_overview.csv")
    fieldnames = [
        "experiment_name",
        "run_id",
        "algorithm_name",
        "agent_name",
        "main_model",
        "case_count",
        "success_rate",
        "success_count",
        "total_tokens",
        # Named for what it is: `total_cost` is a token count times a rate that is a built-in
        # fallback unless `AIBENCH_USD_PER_MTOK*` was set, and the per-run markdown has always
        # said 估 while this column did not.
        "total_cost_usd_estimate",
        "cost_rate_source",
        "total_wall_time_h",
        "selection_is_oracle",
        "relative_success_lift",
        "run_dir",
    ]
    with atomic_write(out, newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(
                {
                    **r,
                    "total_cost_usd_estimate": r.get("total_cost"),
                    "cost_rate_source": (r.get("cost_rate") or {}).get("source"),
                }
            )
    return out


def export_ablation_xlsx(ablation_dir: Path, out_xlsx: Path | None = None) -> Path:
    try:
        from openpyxl import Workbook
    except ImportError as e:
        raise RuntimeError("openpyxl not installed; run: uv add openpyxl") from e

    summary = load_json(ablation_dir / "ablation_summary.json")
    rows = summary.get("runs") or []
    out = out_xlsx or (ablation_dir / "ablation_overview.xlsx")
    wb = Workbook()
    ws = wb.active
    ws.title = "项目效果综述"
    headers = [
        "算法名称",
        "Agent与模型",
        "基础/主模型",
        "Benchmark",
        "Case数",
        "主指标名称",
        "主指标值",
        "总体耗时(h)",
        "总体Token消耗",
        "相对基线收益",
        "总成本",
    ]
    ws.append(headers)
    for r in rows:
        o = r.get("overview_row") or {}
        sr = float(r.get("success_rate") or 0) * 100
        lift = o.get("相对基线收益")
        if lift is None and r.get("relative_success_lift") is not None:
            lift = f"{float(r['relative_success_lift']) * 100:+.1f}pp"
        ws.append(
            [
                o.get("算法名称") or r.get("algorithm_name"),
                o.get("Agent与模型"),
                o.get("基础/主模型") or r.get("main_model"),
                o.get("Benchmark"),
                o.get("Case数") or r.get("case_count"),
                o.get("主指标名称") or "task_success_rate",
                f"{sr:.1f}%",
                o.get("总体耗时(h)") or r.get("total_wall_time_h"),
                o.get("总体Token消耗") or r.get("total_tokens"),
                lift,
                r.get("total_cost"),
            ]
        )
    wb.save(out)
    return out
