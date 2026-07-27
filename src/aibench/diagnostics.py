"""Aggregate per-case failure reasons for human-readable run reports."""

from __future__ import annotations

from collections import Counter
from typing import Any


def aggregate_failures(case_results: list[dict[str, Any]], *, top_n: int = 15) -> dict[str, Any]:
    failed = [r for r in case_results if not r.get("passed")]
    by_category: Counter[str] = Counter()
    by_status: Counter[str] = Counter()
    reasons: Counter[str] = Counter()
    samples: list[dict[str, Any]] = []

    for r in failed:
        cat = r.get("failure_category") or ("infra" if r.get("infra_error") else "unknown")
        by_category[str(cat)] += 1
        by_status[str(r.get("agent_status") or "unknown")] += 1
        detail = ""
        grade = r.get("grade") or {}
        if isinstance(grade, dict):
            detail = str(grade.get("detail") or "")
        if not detail:
            detail = str(r.get("error_message") or "")
        # normalize first line / short key
        first = detail.strip().splitlines()[0] if detail.strip() else "no_detail"
        if first.startswith("missing key_lines"):
            key = "missing_key_lines"
        elif first.startswith("exit="):
            key = f"script_{first[:20]}"
        elif "parse" in first.lower():
            key = "parse_error"
        elif "missing API" in first or "API key" in first:
            key = "missing_api_key"
        else:
            key = first[:80]
        reasons[key] += 1
        if len(samples) < top_n:
            samples.append(
                {
                    "case_id": r.get("case_id"),
                    "failure_category": cat,
                    "agent_status": r.get("agent_status"),
                    "detail": first[:200],
                }
            )

    return {
        "failed_count": len(failed),
        "by_category": dict(by_category.most_common()),
        "by_agent_status": dict(by_status.most_common()),
        "top_reasons": [{"reason": k, "count": v} for k, v in reasons.most_common(top_n)],
        "samples": samples,
    }


def render_failures_md(agg: dict[str, Any]) -> str:
    lines = [
        "## 失败诊断",
        "",
        f"- 失败 case 数: **{agg.get('failed_count', 0)}**",
        "",
        "### 按失败类别",
        "",
        "| 类别 | 数量 |",
        "| --- | ---: |",
    ]
    for k, v in (agg.get("by_category") or {}).items():
        lines.append(f"| {k} | {v} |")
    lines.extend(["", "### Top 原因", "", "| 原因 | 数量 |", "| --- | ---: |"])
    for item in agg.get("top_reasons") or []:
        lines.append(f"| {item['reason']} | {item['count']} |")
    if agg.get("samples"):
        lines.extend(["", "### 样例", ""])
        for s in agg["samples"][:10]:
            lines.append(
                f"- `{s.get('case_id')}` [{s.get('failure_category')}/{s.get('agent_status')}] "
                f"{s.get('detail')}"
            )
    lines.append("")
    return "\n".join(lines)
