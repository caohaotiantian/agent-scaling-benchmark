from __future__ import annotations

import time
import uuid
from datetime import date
from pathlib import Path
from typing import Any

from aibench.agents.registry import create_agent
from aibench.cases import case_set_dir, load_cases
from aibench.grading import grade_case, workspace_inventory
from aibench.io_util import (
    load_yaml,
    relative_to_repo,
    repo_root,
    write_json,
    write_jsonl,
    write_text,
)
from aibench.languages import case_language_is_javascript, unsupported_node_reason
from aibench.models import AgentConfig, AgentRunResult, Case, GradeResult, ModelConfig, RunConfig
from aibench.parallel_util import parallel_map
from aibench.report import (
    build_summary,
    check_summary,
    render_report_md,
    render_summary_tables_json,
)
from aibench.validity import case_fingerprint, estimate_difficulty, set_fingerprint
from aibench.workspace import materialize_workspace


def _run_one_attempt(
    case: Case,
    *,
    case_dir: Path,
    cs: str,
    agent_cfg: AgentConfig,
    model_cfg: ModelConfig,
    max_steps: int,
    max_wall_time_s: float,
    case_retries: int | None = None,
) -> dict[str, Any]:
    """Run one independent sample of a case. Retries only cover infrastructure failures."""
    import os

    # Extra case-level retries for infra_error only (agent already retries HTTP).
    # Run config first, environment second. `case_retries` reached this function as a
    # parameter and nothing ever passed it, so the only way to set the number that decides how
    # an outage is absorbed was an env var — invisible in the manifest the run is reconstructed
    # from. Two runs whose rows differ because one retried four times and the other twice
    # looked like the same configuration.
    if case_retries is None:
        case_retries = os.environ.get("AIBENCH_CASE_RETRY", "2")
    # `max(1, ...)` on both paths, not only the env one. `case_retry: 0` in a run YAML meant a
    # loop that never ran, and `_run_one_attempt` returned `None` into an `assert` — which
    # `python -O` strips, turning a crash into a `None` row.
    try:
        max_case_tries = max(1, int(case_retries))
    except (TypeError, ValueError):
        max_case_tries = 2

    last_row: dict[str, Any] | None = None

    for attempt in range(1, max_case_tries + 1):
        workspace = case_dir / "workspace"
        mat_info: dict[str, Any] | None = None
        mat_error: str | None = None
        baseline: dict[str, str] = {}
        try:
            mat = materialize_workspace(
                case,
                workspace,
                case_set_dir=case_set_dir(cs),
                allow_network=True,
            )
            mat_info = mat.to_dict()
            # Taken before the agent runs: it is what lets the interference gate tell a file the
            # workspace was built with from one the submission added.
            baseline = workspace_inventory(workspace)
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
            try:
                grade = grade_case(case, workspace, baseline=baseline)
            except Exception as e:
                # The only stage of this loop that was not guarded, and the most expensive one
                # to lose: `parallel_map` re-raises a worker's exception out of the executor
                # block, so one case that made `inject_hidden_tests` or `_grade_gold` raise
                # discarded `results.jsonl`, `summary.json` and `report.md` for the whole run
                # after every other case had already been paid for.
                grade = GradeResult(
                    passed=False,
                    mode=case.grader.mode,
                    detail=f"grader raised: {type(e).__name__}: {e}",
                    infra_error=True,
                )
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
            "tier": case.tier,
            "fingerprint": case_fingerprint(case),
            # Carried into the calibration export so a published band can be recomputed from
            # the file. `auto-v0`'s row requires "只取有参考解的 105 条" and nothing identified
            # which 105, so the recipe failed on its own most-cited example.
            "has_reference": bool(case.grader.gold_files),
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
            "test_pass_ratio": grade.test_pass_ratio if grade else None,
            "reward_hack": bool(grade and grade.reward_hack),
            "collection_error": bool(grade and grade.collection_error),
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


# Fields whose per-attempt values are summed rather than taken from the selected attempt:
# running k samples really did cost k samples' worth of budget.
_ADDITIVE_FIELDS = (
    "total_tokens",
    "prompt_tokens",
    "completion_tokens",
    "model_calls",
    "wall_time_s",
    "step_count",
)


#: Strategies that choose using the grader's verdict, which no real system can see at
#: submission time. Under one of these ``success_rate`` is an upper bound, not a submission —
#: identical to ``pass_at_k``, with ``selection_hit_rate`` identically 1.0 wherever it is
#: defined. That is a legitimate quantity to want; it is not comparable with a rate some other
#: configuration actually submitted, which is what `configs/runs/ablation-matrix.yaml`'s
#: `passk-glm52` row was being McNemar'd against three of.
ORACLE_STRATEGIES = frozenset({"best-of-k"})

#: Every strategy `_select_attempt` implements. A name outside this set used to fall through to
#: `first-submit` while the manifest went on recording the name that was asked for, so
#: `selection_strategy: best-of-K` produced a first-submit number filed as an oracle one.
KNOWN_STRATEGIES = frozenset({"first-submit"}) | ORACLE_STRATEGIES


def selection_is_oracle(strategy: str | None) -> bool:
    return str(strategy or "") in ORACLE_STRATEGIES


def _select_attempt(rows: list[dict[str, Any]], strategy: str) -> dict[str, Any]:
    """Pick the attempt the configured strategy would have submitted.

    An attempt that never ran is not a submission, so both strategies skip infra failures
    before choosing; otherwise the folded row would carry an infra failure's grade and
    failure_category while reporting itself as a normal result.

    ``best-of-k`` consults ``passed`` — see :data:`ORACLE_STRATEGIES`. It is kept because the
    oracle bound is worth measuring; every artifact it produces is labelled so it cannot be
    read as a submitted rate. ``first-submit`` returns the first attempt that ran at all, which
    is already the submit-time answer.
    """
    if strategy not in KNOWN_STRATEGIES:
        raise ValueError(
            f"unknown selection_strategy {strategy!r}; known: {sorted(KNOWN_STRATEGIES)}. "
            f"It used to fall through to first-submit while the manifest recorded the typo."
        )
    usable = [r for r in rows if not r.get("infra_error")] or rows
    if selection_is_oracle(strategy):
        return next((r for r in usable if r.get("passed")), usable[0])
    return usable[0]  # first-submit


def _aggregate_attempts(
    rows: list[dict[str, Any]],
    *,
    strategy: str,
) -> dict[str, Any]:
    """Fold k attempts into the one-row-per-case shape every consumer expects.

    ``passed`` stays the outcome of the attempt the strategy would have submitted, so
    ``success_rate`` keeps its meaning and k=1 behaves exactly as before. The oracle view —
    "was it solvable at all in k tries" — lives in ``pass_at_k`` so the two are never confused.
    """
    selected = _select_attempt(rows, strategy)
    row = dict(selected)
    row["selection_is_oracle"] = selection_is_oracle(strategy)

    usable = [r for r in rows if not r.get("infra_error")]
    outcomes = [bool(r.get("passed")) for r in usable]
    row["attempt_count"] = len(rows)
    row["usable_attempt_count"] = len(usable)
    row["pass_at_1"] = (sum(outcomes) / len(outcomes)) if outcomes else None
    row["pass_at_k"] = any(outcomes) if outcomes else False
    row["pass_pow_k"] = all(outcomes) if outcomes else False
    # Did the strategy pick a winner on a case where one existed?
    row["selection_hit"] = bool(selected.get("passed")) if row["pass_at_k"] else None

    for field in _ADDITIVE_FIELDS:
        row[field] = sum(r.get(field) or 0 for r in rows)
    row["infra_error"] = all(r.get("infra_error") for r in rows)
    row["attempts"] = [
        {
            "attempt": i + 1,
            "passed": r.get("passed"),
            "infra_error": r.get("infra_error"),
            "agent_status": r.get("agent_status"),
            "total_tokens": r.get("total_tokens"),
            "wall_time_s": r.get("wall_time_s"),
            "test_pass_ratio": r.get("test_pass_ratio"),
            "reward_hack": r.get("reward_hack"),
        }
        for i, r in enumerate(rows)
    ]
    return row


def _run_one_case(
    case: Case,
    *,
    run_dir: Path,
    cs: str,
    agent_cfg: AgentConfig,
    model_cfg: ModelConfig,
    max_steps: int,
    max_wall_time_s: float,
    attempts: int = 1,
    selection_strategy: str = "first-submit",
    case_retries: int | None = None,
) -> dict[str, Any]:
    """Sample a case ``attempts`` times and fold the results into a single row."""
    case_dir = run_dir / "cases" / case.case_id
    if attempts <= 1:
        return _aggregate_attempts(
            [
                _run_one_attempt(
                    case,
                    case_dir=case_dir,
                    cs=cs,
                    agent_cfg=agent_cfg,
                    model_cfg=model_cfg,
                    max_steps=max_steps,
                    max_wall_time_s=max_wall_time_s,
                    case_retries=case_retries,
                )
            ],
            strategy=selection_strategy,
        )

    rows = [
        _run_one_attempt(
            case,
            case_dir=case_dir / f"attempt-{n}",
            cs=cs,
            agent_cfg=agent_cfg,
            model_cfg=model_cfg,
            max_steps=max_steps,
            max_wall_time_s=max_wall_time_s,
            case_retries=case_retries,
        )
        for n in range(1, attempts + 1)
    ]
    row = _aggregate_attempts(rows, strategy=selection_strategy)
    write_json(case_dir / "result.json", {"case_id": case.case_id, "row": row})
    return row


def run_benchmark(
    *,
    run_config_path: Path | None = None,
    agent_config_path: Path | None = None,
    model_config_path: Path | None = None,
    case_set: str | None = None,
    run_id: str | None = None,
    output_root: Path | None = None,
    case_workers: int | None = None,
    require_grading_env: bool = False,
    experiment_name: str | None = None,
) -> Path:
    root = repo_root()
    run_cfg_path = run_config_path or (root / "configs/runs/baseline.yaml")
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
    if experiment_name:
        # An ablation row names itself; the run config it points at may be shared with another
        # row. Without this, two rows on `baseline.yaml` both wrote `prod-baseline` into their
        # manifest and summary, and the artifacts could not say which row produced them.
        run_cfg.experiment_name = experiment_name
    if run_cfg.selection_strategy not in KNOWN_STRATEGIES:
        # Checked before a single case runs. `_select_attempt` raises too, but that is reached
        # only after the whole matrix has been executed and paid for, and it leaves the run
        # without `results.jsonl` or `summary.json` — the measurements are lost along with the
        # money. A typo in a config is knowable at load time.
        raise ValueError(
            f"unknown selection_strategy {run_cfg.selection_strategy!r} in "
            f"{run_cfg_path.name if run_cfg_path else 'the run config'}; "
            f"known: {sorted(KNOWN_STRATEGIES)}"
        )
    cs = case_set or run_cfg.case_set
    cases = load_cases(cs, validate=True)
    workers = int(case_workers if case_workers is not None else run_cfg.case_workers)

    # Warned before anything is spent, not refused. A `.nvmrc` is advisory and the failure it
    # prevents is silent: below the floor `node --test` discovers no TypeScript test file and
    # exits 0, which is a pass on the defective stub. Measured: five real `.ts` cases flip.
    #
    # `_grade_script` is where that is actually enforced, per case, as `infra_error` — and a
    # per-case refusal is strictly safer than a run-level one, because refusing the whole run
    # makes a set with one `.ts` case among a hundred Python cases ungradable on a machine that
    # can grade the hundred. Nothing false gets recorded either way.
    if (n_js := sum(1 for c in cases if case_language_is_javascript(c.language))) and (
        reason := unsupported_node_reason()
    ):
        print(
            f"[warn] {n_js} of {len(cases)} case(s) in {cs!r} are JavaScript/TypeScript and this "
            f"machine cannot grade them: {reason}. They will be recorded as `infra_error`, not "
            f"as failures; the rest of the set runs normally."
        )

    rid = run_id or f"{run_cfg.experiment_name}-{uuid.uuid4().hex[:8]}"
    started_monotonic = time.monotonic()
    started_at = time.strftime("%Y-%m-%dT%H:%M:%S")
    ts = time.strftime("%Y%m%d_%H%M%S")
    out_root = output_root or (root / "runs")
    run_dir = out_root / f"{run_cfg.benchmark_name}__{ts}_{rid}"
    run_dir.mkdir(parents=True, exist_ok=True)

    sampling = f"temperature={model_cfg.temperature}, max_tokens={model_cfg.max_tokens}"
    # The manifest only needs the content hash. Running the full audit here would re-execute
    # the stub-fail and reference-solution gates — two pytest invocations per case — on every
    # run, which a calibration sweep multiplies by anchors x repeats. Gates belong to
    # `aibench audit-cases`, not to every run.
    try:
        set_fp = set_fingerprint(cases)
    except Exception:
        set_fp = None
    # Recorded *and* compared. The manifest has always carried the measured fingerprint and
    # nothing ever checked it, so a corpus that drifted underneath a config produced a number
    # filed against the set it no longer was — `_revmixed` reproduces 0 of its 31 recorded
    # fingerprints because the prompts were later translated, while still being called
    # `_revmixed` everywhere.
    expected_fp = run_cfg.expected_case_set_fingerprint
    # A `.`-prefixed set is one this harness just materialized — `.ablation-filtered-<set>` and
    # `.calibrating-<set>` are deliberate *subsets*, so their fingerprint cannot equal the
    # parent's and never could. Comparing it refuses every ablation and calibration row on any
    # config that pins a fingerprint, which is precisely the configs an operator is told to pin.
    derived_set = cs.startswith(".")
    if expected_fp and derived_set:
        print(
            f"[info] {cs!r} is a subset this run materialized; skipping the "
            f"`expected_case_set_fingerprint` check, which can only match the full parent set."
        )
    if expected_fp and not derived_set and set_fp != expected_fp:
        raise RuntimeError(
            f"case set {cs!r} has fingerprint {set_fp}, but {run_cfg_path.name} expects "
            f"{expected_fp}. The corpus is not the one this config was written against; "
            f"comparing the result against an earlier number would be comparing two sets. "
            f"Update `expected_case_set_fingerprint` only if the drift is intended."
        )

    from aibench.provenance import environment

    manifest: dict[str, Any] = {
        "run_id": rid,
        "experiment_name": run_cfg.experiment_name,
        "experiment_time": date.today().isoformat(),
        "started_at": started_at,
        # Was `aibench@0.1.0 / agent@{version}` — two literals, identical across all 148
        # manifests on disk, including the ones straddling an adapter fix worth 58 points.
        **environment(),
        "benchmark_name": run_cfg.benchmark_name,
        "case_set": cs,
        "case_set_fingerprint": set_fp,
        "expected_case_set_fingerprint": expected_fp,
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
        "result_dir": relative_to_repo(run_dir),
        "judgment_type": "半确定性",
        "primary_metric_name": "task_success_rate",
    }
    # `configs/grading-env.yaml` is a declaration the run never verified: the grader shells out
    # to `python -m pytest -q` in whatever interpreter happens to be current, so a case built
    # against a promised package that is not installed fails at grading and reads as difficulty.
    # Warned and recorded rather than aborted, because `run_benchmark` is called directly by the
    # test suite and aborting would couple every one of those tests to the installed extras —
    # reintroducing the failure mode of RP-09 in a new place. `--require-grading-env` aborts.
    from aibench.grading_env import grading_env_digest, unsatisfied_promises

    unmet = unsatisfied_promises()
    manifest["grading_env_unsatisfied"] = unmet
    # How many times an infra failure was retried. The docs say this field is why `case_retry`
    # is worth having in the config at all; it was not being written.
    import os as _os

    try:
        manifest["case_retry"] = max(
            1,
            int(
                run_cfg.case_retry
                if run_cfg.case_retry is not None
                else _os.environ.get("AIBENCH_CASE_RETRY", "2")
            ),
        )
    except (TypeError, ValueError):
        manifest["case_retry"] = 2
    # Which builds satisfied the promises, not only which names were promised. Two runs of the
    # same case set can disagree because one had numpy 2.1 and the other 2.3; no artifact said.
    manifest["grading_env_digest"] = grading_env_digest()
    if unmet:
        message = (
            f"configs/grading-env.yaml promises packages this interpreter cannot import: "
            f"{', '.join(unmet)}. Cases importing them will fail at grading, which reads as "
            f"difficulty. Run `uv sync --extra dev --extra grading`."
        )
        if require_grading_env:
            raise RuntimeError(message)
        print(f"[warn] {message}")
    manifest["selection_is_oracle"] = selection_is_oracle(run_cfg.selection_strategy)
    if manifest["selection_is_oracle"]:
        print(
            f"[warn] selection_strategy={run_cfg.selection_strategy!r} picks the attempt the "
            f"grader passed, which no real system can see at submission time. This run's "
            f"success_rate is an oracle upper bound identical to pass@k — do not compare it "
            f"against a configuration that submitted honestly."
        )
    attempts = max(1, int(run_cfg.max_attempts))
    if attempts > 1 and model_cfg.temperature == 0:
        print(
            f"[warn] max_attempts={attempts} with temperature=0: every sample is identical, so "
            f"pass@k collapses onto pass@1. Use a sampling model config (temperature > 0) for "
            f"pass@k to mean anything."
        )
        manifest["sampling_warning"] = (
            "max_attempts>1 at temperature=0; samples are not independent"
        )
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
            attempts=attempts,
            selection_strategy=run_cfg.selection_strategy,
            case_retries=run_cfg.case_retry,
        )

    case_results = parallel_map(_job, cases, workers=workers)
    case_results.sort(key=lambda r: str(r.get("case_id") or ""))

    # Real elapsed time. Summing the agents' own clocks is not elapsed time when `case_workers`
    # is above 1, and it also excludes workspace materialization, `setup_commands` and the
    # grader subprocess entirely.
    # Written first, before anything derived from it. `results.jsonl` is the only artifact that
    # cannot be recomputed — summary, tables and report are all functions of it — so a raise in
    # `build_summary` or `render_report_md` used to discard the whole run's measurements after
    # they had been paid for.
    write_jsonl(run_dir / "results.jsonl", case_results)

    elapsed_wall_s = time.monotonic() - started_monotonic
    manifest["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    manifest["elapsed_wall_time_s"] = elapsed_wall_s
    write_json(run_dir / "run_manifest.json", manifest)

    summary = build_summary(
        run_id=rid,
        run_manifest=manifest,
        case_results=case_results,
        elapsed_wall_s=elapsed_wall_s,
    )
    tables = render_summary_tables_json(summary)
    report_md = render_report_md(summary, case_results)

    summary["report_path"] = relative_to_repo(run_dir / "report.md")
    summary["raw_results_path"] = relative_to_repo(run_dir / "results.jsonl")
    summary["tables"] = tables
    summary["case_set_fingerprint"] = set_fp
    summary["case_workers"] = workers

    write_json(run_dir / "summary.json", summary)
    write_json(run_dir / "tables.json", tables)
    write_text(run_dir / "report.md", report_md)

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
    if grade is not None and getattr(grade, "reward_hack", False):
        return "评测作弊失败"
    if infra:
        return "沙箱基础设施失败" if agent_status != "infra_error" else "LLM服务失败"
    if agent_status == "timeout":
        return "预算耗尽失败"
    if agent_status == "failed":
        return "Agent协议失败"
    if grade is not None and getattr(grade, "collection_error", False):
        # The suite never ran. At run time the harness cannot tell whether the case shipped
        # broken or the submission broke it — a syntax error in the model's own edit produces
        # exactly this — so the category names the observation and blames neither. The
        # audit-time verdict on `metadata.uncollectable_stub` is what separates the two.
        return "测试未执行"
    if grade is not None and not grade.passed:
        return "模型推理失败"
    return "模型推理失败"
