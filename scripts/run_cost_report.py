#!/usr/bin/env python3
"""What it cost to produce the numbers in this repository.

`docs/AUDIT-2026-08-17.md` RP-23: the compute budget to replay this work is ~245M tokens /
42.5k model calls / 151 hours of agent wall-clock, and that figure appears nowhere — it is
computable only from the gitignored `runs/` tree, so nobody deciding whether to attempt a
replay can see it. This is the one aggregation that turns that tree into a publishable number,
and it publishes nothing on its own: it prints, and `--json` writes a file the owner can choose
to commit beside the calibrations.

It is a floor, twice over. It counts only the *measurement* runs, not the generation calls that
produced the case sets; and dollar cost is an estimate at whatever rate
`AIBENCH_USD_PER_MTOK*` names — unset, that is a built-in fallback and not a price. The rate
and its source travel with the number for exactly that reason.

    uv run python scripts/run_cost_report.py
    uv run python scripts/run_cost_report.py --json runs_cost.json
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aibench.io_util import relative_to_repo, repo_root, write_json
from aibench.report import resolved_usd_rate


def _rows(path: Path) -> list[dict[str, Any]]:
    try:
        return [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    except (OSError, json.JSONDecodeError):
        return []


def collect(runs_root: Path) -> dict[str, Any]:
    """Sum every `results.jsonl` under ``runs_root``, grouped by top-level run directory."""
    by_group: dict[str, dict[str, float]] = defaultdict(
        lambda: {"files": 0, "cases": 0, "tokens": 0, "model_calls": 0, "agent_wall_s": 0.0}
    )
    for path in sorted(runs_root.rglob("results.jsonl")):
        group = path.relative_to(runs_root).parts[0]
        rows = _rows(path)
        if not rows:
            continue
        acc = by_group[group]
        acc["files"] += 1
        acc["cases"] += len(rows)
        acc["tokens"] += sum(int(r.get("total_tokens") or 0) for r in rows)
        acc["model_calls"] += sum(int(r.get("model_calls") or 0) for r in rows)
        acc["agent_wall_s"] += sum(float(r.get("wall_time_s") or 0.0) for r in rows)

    total = {"files": 0, "cases": 0, "tokens": 0, "model_calls": 0, "agent_wall_s": 0.0}
    for acc in by_group.values():
        for key in total:
            total[key] += acc[key]

    rate = resolved_usd_rate()
    usd = rate["usd_per_mtok"]
    return {
        "runs_root": relative_to_repo(runs_root),
        "note": (
            "Measurement runs only — the generation calls that produced the case sets are not "
            "counted, so this is a floor. `agent_wall_h` is the sum of the agents' own clocks "
            "and is not elapsed time: cases run `case_workers` at a time."
        ),
        "cost_rate": rate,
        "totals": {
            **{k: (round(v, 1) if isinstance(v, float) else v) for k, v in total.items()},
            "agent_wall_h": round(total["agent_wall_s"] / 3600.0, 1),
            "usd_estimate": (
                round(total["tokens"] / 1_000_000.0 * usd, 2) if usd is not None else None
            ),
        },
        "by_run": {
            name: {
                **{k: (round(v, 1) if isinstance(v, float) else v) for k, v in acc.items()},
                "agent_wall_h": round(acc["agent_wall_s"] / 3600.0, 2),
            }
            for name, acc in sorted(by_group.items())
        },
    }


def render(report: dict[str, Any]) -> str:
    t = report["totals"]
    lines = [
        f"runs root: {report['runs_root']}",
        f"result files: {t['files']}   case rows: {t['cases']}",
        f"tokens: {t['tokens']:,}   model calls: {t['model_calls']:,}",
        f"agent wall-clock: {t['agent_wall_h']:,} h  (sum of agent clocks, not elapsed)",
        f"USD estimate: {t['usd_estimate']}   rate: {report['cost_rate']['source']}",
        "",
        f"{'run':<44} {'tokens':>14} {'calls':>8} {'agent h':>9}",
    ]
    for name, acc in report["by_run"].items():
        lines.append(
            f"{name[:44]:<44} {acc['tokens']:>14,} {acc['model_calls']:>8,} {acc['agent_wall_h']:>9.2f}"
        )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Compute cost and time for the runs on disk.")
    parser.add_argument("--runs-root", type=Path, default=repo_root() / "runs")
    parser.add_argument("--json", type=Path, default=None, help="Also write the report here")
    args = parser.parse_args()

    if not args.runs_root.is_dir():
        print(
            f"no runs directory at {args.runs_root}. `runs/` is gitignored, so a clone has "
            f"none — this report can only be produced on a machine that did the runs."
        )
        return 1
    report = collect(args.runs_root)
    if not report["totals"]["files"]:
        print(f"no results.jsonl found under {args.runs_root}")
        return 1
    print(render(report))
    if args.json:
        write_json(args.json, report)
        print(f"\nwrote {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
