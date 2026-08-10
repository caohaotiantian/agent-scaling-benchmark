"""Repeated sampling: attempt folding, pass@k, and selection accounting."""

from pathlib import Path

from aibench.io_util import load_json, repo_root
from aibench.report import build_summary
from aibench.runner import _aggregate_attempts, _select_attempt, run_benchmark


def _attempt(passed, *, tokens=100, infra=False, status="completed"):
    return {
        "case_id": "c1",
        "passed": passed,
        "infra_error": infra,
        "agent_status": status,
        "total_tokens": tokens,
        "prompt_tokens": tokens // 2,
        "completion_tokens": tokens // 2,
        "model_calls": 1,
        "wall_time_s": 1.0,
        "step_count": 2,
        "test_pass_ratio": 1.0 if passed else 0.0,
        "reward_hack": False,
    }


def test_single_attempt_behaves_exactly_as_before():
    row = _aggregate_attempts([_attempt(True)], strategy="first-submit")
    assert row["passed"] is True
    assert row["attempt_count"] == 1
    assert row["pass_at_1"] == 1.0
    assert row["pass_at_k"] is True
    assert row["pass_pow_k"] is True
    assert row["total_tokens"] == 100


def test_first_submit_takes_the_first_attempt_even_when_a_later_one_passes():
    rows = [_attempt(False), _attempt(True), _attempt(True)]
    row = _aggregate_attempts(rows, strategy="first-submit")
    assert row["passed"] is False, "success_rate must reflect what the strategy submitted"
    assert row["pass_at_1"] == 2 / 3
    assert row["pass_at_k"] is True
    assert row["pass_pow_k"] is False
    assert row["selection_hit"] is False, "a winner existed and first-submit missed it"


def test_best_of_k_submits_a_passing_attempt():
    rows = [_attempt(False), _attempt(True)]
    row = _aggregate_attempts(rows, strategy="best-of-k")
    assert row["passed"] is True
    assert row["selection_hit"] is True


def test_selection_hit_is_undefined_when_no_attempt_passed():
    row = _aggregate_attempts([_attempt(False), _attempt(False)], strategy="best-of-k")
    assert row["pass_at_k"] is False
    assert row["selection_hit"] is None


def test_cost_is_the_sum_of_every_attempt_not_just_the_submitted_one():
    """Running k samples really did cost k samples' worth of budget."""
    rows = [_attempt(False, tokens=100), _attempt(True, tokens=250)]
    row = _aggregate_attempts(rows, strategy="best-of-k")
    assert row["total_tokens"] == 350
    assert row["model_calls"] == 2
    assert row["wall_time_s"] == 2.0
    assert row["step_count"] == 4


def test_infra_errors_are_excluded_from_the_rates_but_still_cost_tokens():
    rows = [_attempt(False, infra=True, tokens=10), _attempt(True, tokens=90)]
    row = _aggregate_attempts(rows, strategy="first-submit")
    assert row["usable_attempt_count"] == 1
    assert row["pass_at_1"] == 1.0, "the broken sample is not evidence the case is hard"
    assert row["total_tokens"] == 100
    assert row["infra_error"] is False


def test_case_is_infra_only_when_every_attempt_failed_to_run():
    rows = [_attempt(False, infra=True), _attempt(False, infra=True)]
    row = _aggregate_attempts(rows, strategy="first-submit")
    assert row["infra_error"] is True
    assert row["pass_at_1"] is None
    assert row["pass_at_k"] is False


def test_per_attempt_detail_is_retained():
    rows = [_attempt(False), _attempt(True)]
    row = _aggregate_attempts(rows, strategy="best-of-k")
    assert [a["attempt"] for a in row["attempts"]] == [1, 2]
    assert [a["passed"] for a in row["attempts"]] == [False, True]


def test_select_attempt_falls_back_to_the_first_when_none_passed():
    rows = [_attempt(False), _attempt(False)]
    assert _select_attempt(rows, "best-of-k") is rows[0]


def test_summary_reports_the_scaling_headroom():
    rows = [
        _aggregate_attempts([_attempt(False), _attempt(True)], strategy="first-submit"),
        _aggregate_attempts([_attempt(True), _attempt(True)], strategy="first-submit"),
    ]
    rows[1]["case_id"] = "c2"
    s = build_summary(run_id="r", run_manifest={}, case_results=rows)

    assert s["success_rate"] == 0.5  # first-submit submitted a failure on c1
    assert s["pass_at_1"] == 0.75  # (0.5 + 1.0) / 2
    assert s["pass_at_k"] == 1.0  # both solvable within 2 tries
    assert s["pass_pow_k"] == 0.5
    assert s["oracle_success_count"] == 2
    assert s["selection_hit_rate"] == 0.5
    assert s["attempts_per_case"] == 2.0


def test_summary_keys_stay_present_for_a_single_sample_run():
    row = _aggregate_attempts([_attempt(True)], strategy="first-submit")
    s = build_summary(run_id="r", run_manifest={}, case_results=[row])
    assert s["success_rate"] == s["pass_at_1"] == s["pass_at_k"] == 1.0
    assert s["selection_hit_rate"] == 1.0
    assert s["attempts_per_case"] == 1.0


def test_multi_attempt_run_writes_per_attempt_artifacts(tmp_path: Path):
    run_dir = run_benchmark(
        run_config_path=repo_root() / "tests/fixtures/configs/runs/attempts.mock.yaml",
        case_set="seed-v0",
        run_id="attempts-mock",
        output_root=tmp_path,
    )
    summary = load_json(run_dir / "summary.json")
    assert summary["attempts_per_case"] == 3
    assert summary["max_attempts_observed"] == 3
    # The mock agent is deterministic, so repeated sampling must not change the verdict.
    assert summary["pass_at_1"] == summary["pass_at_k"] == summary["success_rate"]

    case_dirs = sorted((run_dir / "cases").iterdir())
    assert (case_dirs[0] / "attempt-1" / "result.json").is_file()
    assert (case_dirs[0] / "attempt-3" / "result.json").is_file()
    assert load_json(case_dirs[0] / "result.json")["row"]["attempt_count"] == 3


def test_temperature_zero_multi_attempt_is_flagged_in_the_manifest(tmp_path: Path):
    run_dir = run_benchmark(
        run_config_path=repo_root() / "tests/fixtures/configs/runs/attempts.mock.yaml",
        case_set="seed-v0",
        run_id="attempts-warn",
        output_root=tmp_path,
    )
    manifest = load_json(run_dir / "run_manifest.json")
    assert "sampling_warning" in manifest


def test_selection_skips_an_infra_attempt_rather_than_submitting_it():
    """A run that never happened is not a submission; folding it in would tag the row with
    an infra failure's metadata while reporting itself as a normal result."""
    rows = [_attempt(False, infra=True, status="infra_error"), _attempt(True)]
    row = _aggregate_attempts(rows, strategy="first-submit")
    assert row["passed"] is True
    assert row["agent_status"] == "completed"
    assert row["infra_error"] is False


def test_selection_falls_back_when_every_attempt_is_infra():
    rows = [_attempt(False, infra=True, status="infra_error")] * 2
    row = _aggregate_attempts(rows, strategy="best-of-k")
    assert row["infra_error"] is True
    assert row["agent_status"] == "infra_error"
