from __future__ import annotations

import time
import uuid
from datetime import date
from pathlib import Path
from typing import Any

from aibench.agents.registry import create_agent
from aibench.cases import case_set_dir, load_cases
from aibench.grading import grade_case
from aibench.io_util import load_yaml, repo_root, write_json, write_jsonl
from aibench.models import AgentConfig, AgentRunResult, Case, ModelConfig, RunConfig
from aibench.parallel_util import parallel_map
from aibench.report import (
    build_summary,
    check_summary,
    render_report_md,
    render_summary_tables_json,
)
from aibench.validity import case_fingerprint, estimate_difficulty
from aibench.workspace import materialize_workspace


def _run_one_case(
    case: Case,
    *,
    run_dir: Path,
    cs: str,
    agent_cfg: AgentConfig,
    model_cfg: ModelConfig,
    max_steps: int,
    max_wall_time_s: float,
    case_retries: int | None = None,
) -> dict[str, Any]:
    """Execute a single case in isolation (safe for thread pool)."""
    import os

    # Extra case-level retries for infra_error only (agent already retries HTTP).
    max_case_tries = case_retries
    if max_case_tries is None:
        max_case_tries = max(1, int(os.environ.get("AIBENCH_CASE_RETRY", "2")))

    case_dir = run_dir / "cases" / case.case_id
    last_row: dict[str, Any] | None = None

    for attempt in range(1, max_case_tries + 1):
        workspace = case_dir / "workspace"
        mat_info: dict[str, Any] | None = None
        mat_error: str | None = None
        try:
            mat = materialize_workspace(
                case,
                workspace,
                case_set_dir=case_set_dir(cs),
                allow_network=True,
            )
            mat_info = mat.to_dict()
            write_json(case_dir / "workspace_manifest.json", mat_info)
        except Exception as e:
            mat_error = str(e)
            write_json(
                case_dir / "workspace_manifest.json",
                {"error": mat_error, "sources_applied": []},
            )

        if mat_error:
            agent_result = AgentRunResult(
                status="infra_error",
                error_message=f"workspace materialize failed: {mat_error}",
            )
        else:
            agent = create_agent(agent_cfg, model_cfg)
            try:
                agent_result = agent.run(
                    case,
                    workspace,
                    max_steps=max_steps,
                    max_wall_time_s=max_wall_time_s,
                )
            except Exception as e:
                agent_result = AgentRunResult(
                    status="infra_error",
                    error_message=str(e),
                )

        infra = agent_result.status == "infra_error"
        grade = None
        if not infra:
            grade = grade_case(case, workspace)
            if grade.infra_error:
                infra = True

        passed = bool(grade and grade.passed and not infra)
        judge_score = None
        if grade and grade.mode == "llm_judge" and grade.score is not None:
            judge_score = grade.score

        difficulty = case.metadata.get("difficulty") or estimate_difficulty(case)
        row = {
            "case_id": case.case_id,
            "task_type": case.task_type,
            "language": case.language,
            "difficulty": difficulty,
            "fingerprint": case.metadata.get("fingerprint") or case_fingerprint(case),
            "agent_status": agent_result.status,
            "passed": passed,
            "infra_error": infra,
            "empty_patch": agent_result.empty_patch,
            "total_tokens": agent_result.usage.total_tokens,
            "prompt_tokens": agent_result.usage.prompt_tokens,
            "completion_tokens": agent_result.usage.completion_tokens,
            "model_calls": agent_result.usage.model_calls,
            "wall_time_s": agent_result.wall_time_s,
            "step_count": len(agent_result.steps),
            "judge_score": judge_score,
            "grade": grade.to_dict() if grade else None,
            "error_message": agent_result.error_message,
            "failure_category": _failure_category(infra, passed, agent_result.status, grade),
            "workspace_sources": (mat_info or {}).get("sources_applied"),
            "workspace_warnings": (mat_info or {}).get("warnings"),
            "attempt": attempt,
        }
        write_json(
            case_dir / "result.json",
            {
                "case_id": case.case_id,
                "agent": agent_result.to_dict(),
                "grade": grade.to_dict() if grade else None,
                "workspace": mat_info,
                "row": row,
            },
        )
        last_row = row
        if not infra or attempt >= max_case_tries:
            return row
        print(
            f"[retry] case {case.case_id} infra_error attempt {attempt}/{max_case_tries}: "
            f"{agent_result.error_message}"
        )

    assert last_row is not None
    return last_row


def run_benchmark(
    *,
    run_config_path: Path | None = None,
    agent_config_path: Path | None = None,
    model_config_path: Path | None = None,
    case_set: str | None = None,
    run_id: str | None = None,
    output_root: Path | None = None,
    case_workers: int | None = None,
) -> Path:
    root = repo_root()
    run_cfg_path = run_config_path or (root / "configs/runs/seed-baseline.yaml")
    run_raw = load_yaml(run_cfg_path)
    run_cfg = RunConfig.from_dict(run_raw)

    agent_path = Path(agent_config_path or root / run_cfg.agent_config_path)
    model_path = Path(model_config_path or root / run_cfg.model_config_path)
    if not agent_path.is_absolute():
        agent_path = root / agent_path
    if not model_path.is_absolute():
        model_path = root / model_path

    agent_cfg = AgentConfig.from_dict(load_yaml(agent_path))
    model_cfg = ModelConfig.from_dict(load_yaml(model_path))
    cs = case_set or run_cfg.case_set
    cases = load_cases(cs, validate=True)
    workers = int(case_workers if case_workers is not None else run_cfg.case_workers)

    rid = run_id or f"{run_cfg.experiment_name}-{uuid.uuid4().hex[:8]}"
    ts = time.strftime("%Y%m%d_%H%M%S")
    out_root = output_root or (root / "runs")
    run_dir = out_root / f"{run_cfg.benchmark_name}__{ts}_{rid}"
    run_dir.mkdir(parents=True, exist_ok=True)

    sampling = f"temperature={model_cfg.temperature}, max_tokens={model_cfg.max_tokens}"
    from aibench.validity import audit_case_set

    try:
        set_fp = audit_case_set(cs).get("content_fingerprint")
    except Exception:
        set_fp = None

    manifest: dict[str, Any] = {
        "run_id": rid,
        "experiment_name": run_cfg.experiment_name,
        "experiment_time": date.today().isoformat(),
        "code_version": f"aibench@0.1.0 / agent@{agent_cfg.version}",
        "benchmark_name": run_cfg.benchmark_name,
        "case_set": cs,
        "case_set_fingerprint": set_fp,
        "case_count": len(cases),
        "case_workers": workers,
        "grouping": run_cfg.grouping,
        "algorithm_name": run_cfg.algorithm_name,
        "algorithm_version": run_cfg.algorithm_version,
        "budget_axis": run_cfg.budget_axis,
        "budget_value": run_cfg.budget_value,
        "branches": run_cfg.branches,
        "max_attempts": run_cfg.max_attempts,
        "max_steps": run_cfg.max_steps,
        "max_wall_time_s": run_cfg.max_wall_time_s,
        "selection_strategy": run_cfg.selection_strategy,
        "agent_name": agent_cfg.name,
        "agent_version": agent_cfg.version,
        "agent_adapter": agent_cfg.adapter,
        "main_model": model_cfg.model,
        "draft_model": None,
        "panel_models": None,
        "aggregator_model": None,
        "verifier_model": None,
        "fallback_model": None,
        "model_combo_summary": f"main={model_cfg.model}",
        "sampling_params": sampling,
        "model_config_name": model_cfg.name,
        "result_dir": str(run_dir),
        "judgment_type": "半确定性",
        "primary_metric_name": "task_success_rate",
    }
    write_json(run_dir / "run_manifest.json", manifest)

    def _job(case: Case) -> dict[str, Any]:
        return _run_one_case(
            case,
            run_dir=run_dir,
            cs=cs,
            agent_cfg=agent_cfg,
            model_cfg=model_cfg,
            max_steps=run_cfg.max_steps,
            max_wall_time_s=run_cfg.max_wall_time_s,
        )

    case_results = parallel_map(_job, cases, workers=workers)
    case_results.sort(key=lambda r: str(r.get("case_id") or ""))

    summary = build_summary(run_id=rid, run_manifest=manifest, case_results=case_results)
    tables = render_summary_tables_json(summary)
    report_md = render_report_md(summary, case_results)

    summary["report_path"] = str(run_dir / "report.md")
    summary["raw_results_path"] = str(run_dir / "results.jsonl")
    summary["tables"] = tables
    summary["case_set_fingerprint"] = set_fp
    summary["case_workers"] = workers

    write_json(run_dir / "summary.json", summary)
    write_jsonl(run_dir / "results.jsonl", case_results)
    write_json(run_dir / "tables.json", tables)
    (run_dir / "report.md").write_text(report_md, encoding="utf-8")

    problems = check_summary(summary)
    if problems:
        raise RuntimeError("summary incomplete: " + "; ".join(problems))

    return run_dir


def _failure_category(
    infra: bool,
    passed: bool,
    agent_status: str,
    grade: Any,
) -> str | None:
    if passed:
        return None
    if infra:
        return "沙箱基础设施失败" if agent_status != "infra_error" else "LLM服务失败"
    if agent_status == "timeout":
        return "预算耗尽失败"
    if agent_status == "failed":
        return "Agent协议失败"
    if grade is not None and not grade.passed:
        return "模型推理失败"
    return "模型推理失败"
