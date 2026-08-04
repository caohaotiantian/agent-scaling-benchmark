from __future__ import annotations

import os
from typing import Any

from aibench.diagnostics import aggregate_failures, render_failures_md
from aibench.stats import format_wilson_ci, stratify_results

SUMMARY_REQUIRED_KEYS = [
    "run_id",
    "experiment_name",
    "benchmark_name",
    "case_set",
    "case_count",
    "effective_case_count",
    "success_count",
    "success_rate",
    "primary_metric_name",
    "primary_metric_value",
    "total_tokens",
    "total_wall_time_h",
    "agent_name",
    "main_model",
    "algorithm_name",
]


def check_summary(summary: dict[str, Any]) -> list[str]:
    missing = [k for k in SUMMARY_REQUIRED_KEYS if k not in summary]
    return [f"missing key: {k}" for k in missing]


def build_summary(
    *,
    run_id: str,
    run_manifest: dict[str, Any],
    case_results: list[dict[str, Any]],
) -> dict[str, Any]:
    case_count = len(case_results)
    infra = [r for r in case_results if r.get("infra_error")]
    effective = [r for r in case_results if not r.get("infra_error")]
    successes = [r for r in effective if r.get("passed")]
    effective_n = len(effective)
    success_n = len(successes)
    success_rate = (success_n / effective_n) if effective_n else 0.0

    total_tokens = sum(int(r.get("total_tokens") or 0) for r in case_results)
    total_wall_s = sum(float(r.get("wall_time_s") or 0.0) for r in case_results)
    total_steps = sum(int(r.get("step_count") or 0) for r in case_results)
    total_model_calls = sum(int(r.get("model_calls") or 0) for r in case_results)
    empty_patch = sum(1 for r in case_results if r.get("empty_patch"))
    completed = sum(1 for r in case_results if r.get("agent_status") == "completed")

    ratings = [r["judge_score"] for r in case_results if r.get("judge_score") is not None]
    mean_rating = sum(ratings) / len(ratings) if ratings else None

    avg_tokens = (total_tokens / effective_n) if effective_n else 0.0
    avg_tokens_success = (total_tokens / success_n) if success_n else None
    avg_wall_min = (total_wall_s / 60.0 / effective_n) if effective_n else 0.0
    throughput = (case_count / (total_wall_s / 3600.0)) if total_wall_s > 0 else None

    m = run_manifest
    summary: dict[str, Any] = {
        # 实验标识
        "run_id": run_id,
        "experiment_name": m.get("experiment_name"),
        "experiment_time": m.get("experiment_time"),
        "code_version": m.get("code_version"),
        # Benchmark 口径
        "benchmark_name": m.get("benchmark_name"),
        "case_set": m.get("case_set"),
        "case_count": case_count,
        "effective_case_count": effective_n,
        "grouping": m.get("grouping"),
        # 算法配置
        "algorithm_name": m.get("algorithm_name"),
        "algorithm_version": m.get("algorithm_version"),
        "budget_axis": m.get("budget_axis"),
        "budget_value": m.get("budget_value"),
        "branches": m.get("branches"),
        "max_attempts": m.get("max_attempts"),
        "max_steps": m.get("max_steps"),
        "selection_strategy": m.get("selection_strategy"),
        # Agent 与模型
        "agent_name": m.get("agent_name"),
        "agent_version": m.get("agent_version"),
        "main_model": m.get("main_model"),
        "draft_model": m.get("draft_model"),
        "panel_models": m.get("panel_models"),
        "aggregator_model": m.get("aggregator_model"),
        "verifier_model": m.get("verifier_model"),
        "fallback_model": m.get("fallback_model"),
        "model_combo_summary": m.get("model_combo_summary"),
        "sampling_params": m.get("sampling_params"),
        # 主质量指标
        "judgment_type": "半确定性",
        "primary_metric_name": "task_success_rate",
        "primary_metric_value": success_rate,
        "success_count": success_n,
        "success_rate": success_rate,
        "completed_count": completed,
        "completion_rate": (completed / effective_n) if effective_n else 0.0,
        "empty_patch_count": empty_patch,
        "infra_error_count": len(infra),
        "infra_error_rate": (len(infra) / case_count) if case_count else 0.0,
        "mean_rating": mean_rating,
        "confidence_interval": format_wilson_ci(success_n, effective_n),
        "stratified_by_task_type": stratify_results(case_results, key="task_type"),
        "stratified_by_difficulty": stratify_results(case_results, key="difficulty"),
        "stratified_by_tier": stratify_results(case_results, key="tier"),
        "reward_hack_count": sum(1 for r in case_results if r.get("reward_hack")),
        "judgment_agreement": None,
        "baseline_win_rate": None,
        # Scaling 收益（单路径 baseline 默认空）
        "relative_success_lift": None,
        "oracle_success_count": None,
        "oracle_success_rate": None,
        "selection_hit_rate": None,
        "unique_new_successes": None,
        "regression_failures": None,
        "net_gain": None,
        # 成本效率
        "total_tokens": total_tokens,
        "avg_tokens_per_case": avg_tokens,
        "avg_tokens_per_success": avg_tokens_success,
        "total_cost": _estimate_cost_usd(total_tokens),
        "avg_cost_per_case": None,
        "token_amplification": None,
        # 时间效率
        "total_wall_time_h": total_wall_s / 3600.0,
        "throughput_cases_per_h": throughput,
        "avg_wall_min_per_case": avg_wall_min,
        # Agent 行为
        "total_steps": total_steps,
        "avg_steps_per_case": (total_steps / effective_n) if effective_n else 0.0,
        "total_model_calls": total_model_calls,
        "avg_model_calls_per_case": (total_model_calls / effective_n) if effective_n else 0.0,
        # 产物
        "report_path": None,
        "result_dir": m.get("result_dir"),
        "raw_results_path": None,
        "failure_diagnostics": aggregate_failures(case_results),
    }
    if summary["total_cost"] is not None and effective_n:
        summary["avg_cost_per_case"] = summary["total_cost"] / effective_n
    return summary


def _estimate_cost_usd(total_tokens: int) -> float | None:
    """Rough USD estimate from env rates (per 1M tokens)."""
    try:
        # blended rate if only one set; else (in+out)/2
        blended = os.environ.get("AIBENCH_USD_PER_MTOK")
        if blended:
            return total_tokens / 1_000_000.0 * float(blended)
        pin = float(os.environ.get("AIBENCH_USD_PER_MTOK_INPUT", "0.5"))
        pout = float(os.environ.get("AIBENCH_USD_PER_MTOK_OUTPUT", "1.5"))
        # unknown split → use average
        return total_tokens / 1_000_000.0 * ((pin + pout) / 2.0)
    except Exception:
        return None


def render_report_md(summary: dict[str, Any], case_results: list[dict[str, Any]]) -> str:
    sr = summary.get("success_rate") or 0.0
    lines = [
        f"# Benchmark Report: {summary.get('run_id')}",
        "",
        f"- Experiment: {summary.get('experiment_name')}",
        f"- Benchmark: {summary.get('benchmark_name')}",
        f"- Case set: {summary.get('case_set')}",
        f"- Algorithm: {summary.get('algorithm_name')} ({summary.get('algorithm_version')})",
        f"- Agent: {summary.get('agent_name')}@{summary.get('agent_version')}",
        f"- Main model: {summary.get('main_model')}",
        "",
        "## 项目效果综述表（单行）",
        "",
        "| 算法名称 | Agent与模型 | 基础/主模型 | Benchmark | Case数 | 主指标名称 | 主指标值 | 总体耗时(h) | 总体Token消耗 | 相对基线收益 |",
        "| --- | --- | --- | --- | ---: | --- | --- | ---: | ---: | --- |",
        (
            f"| {summary.get('algorithm_name')} "
            f"| {summary.get('agent_name')}｜main={summary.get('main_model')} "
            f"| {summary.get('main_model')} "
            f"| {summary.get('benchmark_name')} "
            f"| {summary.get('case_count')} "
            f"| {summary.get('primary_metric_name')} "
            f"| {sr * 100:.1f}% "
            f"| {float(summary.get('total_wall_time_h') or 0):.6f} "
            f"| {summary.get('total_tokens')} "
            f"|  |"
        ),
        "",
        "## 通用结果总表（关键字段）",
        "",
        "| 字段 | 值 |",
        "| --- | --- |",
        f"| 运行 ID | {summary.get('run_id')} |",
        f"| 实验名称 | {summary.get('experiment_name')} |",
        f"| 实验时间 | {summary.get('experiment_time')} |",
        f"| 代码版本 | {summary.get('code_version')} |",
        f"| Benchmark 名称 | {summary.get('benchmark_name')} |",
        f"| Case 集合 | {summary.get('case_set')} |",
        f"| Case 数量 | {summary.get('case_count')} |",
        f"| 有效 Case 数 | {summary.get('effective_case_count')} |",
        f"| 分组口径 | {summary.get('grouping')} |",
        f"| 算法名称 | {summary.get('algorithm_name')} |",
        f"| 算法版本 | {summary.get('algorithm_version')} |",
        f"| 预算轴 | {summary.get('budget_axis')} |",
        f"| 预算值 | {summary.get('budget_value')} |",
        f"| 分支数 | {summary.get('branches')} |",
        f"| 最大 Attempt 数 | {summary.get('max_attempts')} |",
        f"| 最大 Step 数 | {summary.get('max_steps')} |",
        f"| 选择策略 | {summary.get('selection_strategy')} |",
        f"| Agent 名称 | {summary.get('agent_name')} |",
        f"| Agent 版本 | {summary.get('agent_version')} |",
        f"| 主模型 | {summary.get('main_model')} |",
        f"| 模型组合摘要 | {summary.get('model_combo_summary')} |",
        f"| 采样参数 | {summary.get('sampling_params')} |",
        f"| 评判类型 | {summary.get('judgment_type')} |",
        f"| 主指标名称 | {summary.get('primary_metric_name')} |",
        f"| 主指标值 | {sr * 100:.1f}% |",
        f"| 成功数 | {summary.get('success_count')} |",
        f"| 成功率 | {sr * 100:.1f}% |",
        f"| 完成数 | {summary.get('completed_count')} |",
        f"| 空 Patch 数 | {summary.get('empty_patch_count')} |",
        f"| 基础设施错误数 | {summary.get('infra_error_count')} |",
        f"| 总 Token | {summary.get('total_tokens')} |",
        f"| 平均 Token/Case | {float(summary.get('avg_tokens_per_case') or 0):.1f} |",
        f"| 平均 Token/成功 Case | {summary.get('avg_tokens_per_success')} |",
        f"| 总墙钟(h) | {float(summary.get('total_wall_time_h') or 0):.6f} |",
        f"| 平均耗时/Case(min) | {float(summary.get('avg_wall_min_per_case') or 0):.4f} |",
        f"| 总 Step 数 | {summary.get('total_steps')} |",
        f"| 平均 Step/Case | {float(summary.get('avg_steps_per_case') or 0):.2f} |",
        f"| 总模型调用次数 | {summary.get('total_model_calls')} |",
        f"| 总成本(USD估) | {summary.get('total_cost')} |",
        f"| 成功率 95% CI | {summary.get('confidence_interval')} |",
        f"| Case set fingerprint | {summary.get('case_set_fingerprint')} |",
        "",
        "## 分层成功率",
        "",
    ]
    for title, key in (
        ("tier", "stratified_by_tier"),
        ("task_type", "stratified_by_task_type"),
        ("difficulty", "stratified_by_difficulty"),
    ):
        strata = summary.get(key) or {}
        lines.append(f"### by {title}")
        lines.append("")
        lines.append("| 分层 | n | 成功 | 成功率 | 95% CI |")
        lines.append("| --- | ---: | ---: | ---: | --- |")
        for label, st in strata.items():
            lines.append(
                f"| {label} | {st.get('n')} | {st.get('successes')} "
                f"| {float(st.get('success_rate') or 0) * 100:.1f}% | {st.get('confidence_interval')} |"
            )
        lines.append("")
    lines.extend(
        [
            "## Case 明细",
            "",
            "| case_id | tier | passed | infra_error | tokens | wall_s | steps | 测试通过比 |",
            "| --- | --- | --- | --- | ---: | ---: | ---: | ---: |",
        ]
    )
    for r in case_results:
        ratio = r.get("test_pass_ratio")
        lines.append(
            f"| {r.get('case_id')} | {r.get('tier') or '-'} | {r.get('passed')} "
            f"| {r.get('infra_error')} "
            f"| {r.get('total_tokens')} | {float(r.get('wall_time_s') or 0):.4f} "
            f"| {r.get('step_count')} | {f'{ratio:.2f}' if ratio is not None else '-'} |"
        )
    lines.append("")
    diag = summary.get("failure_diagnostics")
    if diag:
        lines.append(render_failures_md(diag))
    return "\n".join(lines)


def render_summary_tables_json(summary: dict[str, Any]) -> dict[str, Any]:
    """Machine-readable rows aligned with project table schemas."""
    sr = float(summary.get("success_rate") or 0.0)
    overview_row = {
        "算法名称": summary.get("algorithm_name"),
        "Agent与模型": f"{summary.get('agent_name')}｜main={summary.get('main_model')}",
        "基础/主模型": summary.get("main_model"),
        "Benchmark": summary.get("benchmark_name"),
        "Case数": summary.get("case_count"),
        "主指标名称": summary.get("primary_metric_name"),
        "主指标值": f"{sr * 100:.1f}%",
        "总体耗时(h)": summary.get("total_wall_time_h"),
        "总体Token消耗": summary.get("total_tokens"),
        "相对基线收益": summary.get("relative_success_lift"),
    }
    general_row = {
        "运行ID": summary.get("run_id"),
        "实验名称": summary.get("experiment_name"),
        "实验时间": summary.get("experiment_time"),
        "代码版本": summary.get("code_version"),
        "Benchmark名称": summary.get("benchmark_name"),
        "Case集合": summary.get("case_set"),
        "Case数量": summary.get("case_count"),
        "有效Case数": summary.get("effective_case_count"),
        "分组口径": summary.get("grouping"),
        "算法名称": summary.get("algorithm_name"),
        "算法版本": summary.get("algorithm_version"),
        "预算轴": summary.get("budget_axis"),
        "预算值": summary.get("budget_value"),
        "分支数": summary.get("branches"),
        "最大Attempt数": summary.get("max_attempts"),
        "最大Step数": summary.get("max_steps"),
        "选择策略": summary.get("selection_strategy"),
        "Agent名称": summary.get("agent_name"),
        "Agent版本": summary.get("agent_version"),
        "主模型": summary.get("main_model"),
        "草稿模型": summary.get("draft_model"),
        "Panel模型": summary.get("panel_models"),
        "聚合模型": summary.get("aggregator_model"),
        "验证模型": summary.get("verifier_model"),
        "Fallback模型": summary.get("fallback_model"),
        "模型组合摘要": summary.get("model_combo_summary"),
        "采样参数": summary.get("sampling_params"),
        "评判类型": summary.get("judgment_type"),
        "主指标名称": summary.get("primary_metric_name"),
        "主指标值": sr,
        "成功数": summary.get("success_count"),
        "成功率": summary.get("success_rate"),
        "完成数": summary.get("completed_count"),
        "完成率": summary.get("completion_rate"),
        "空Patch数": summary.get("empty_patch_count"),
        "基础设施错误数": summary.get("infra_error_count"),
        "基础设施错误率": summary.get("infra_error_rate"),
        "总Token": summary.get("total_tokens"),
        "平均Token/Case": summary.get("avg_tokens_per_case"),
        "平均Token/成功Case": summary.get("avg_tokens_per_success"),
        "总墙钟": summary.get("total_wall_time_h"),
        "吞吐": summary.get("throughput_cases_per_h"),
        "平均耗时/Case": summary.get("avg_wall_min_per_case"),
        "总Step数": summary.get("total_steps"),
        "平均Step/Case": summary.get("avg_steps_per_case"),
        "总模型调用次数": summary.get("total_model_calls"),
        "平均模型调用/Case": summary.get("avg_model_calls_per_case"),
        "结果目录": summary.get("result_dir"),
    }
    return {"overview_row": overview_row, "general_row": general_row}
