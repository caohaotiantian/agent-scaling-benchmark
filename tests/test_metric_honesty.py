"""A reported number says which question it answers.

Every test here fails at `982a9c4`.
"""

from __future__ import annotations

from typing import Any

from aibench.ablation import (
    _render_ablation_report,
    compare_runs_pairwise,
    diff_axes_against_baseline,
)
from aibench.report import build_summary, render_report_md, resolved_usd_rate
from aibench.runner import _aggregate_attempts, selection_is_oracle


def _attempt(passed: bool, **over: Any) -> dict[str, Any]:
    row = {
        "case_id": over.pop("case_id", "c1"),
        "passed": passed,
        "infra_error": False,
        "agent_status": "completed",
        "total_tokens": 100,
        "wall_time_s": 1.0,
        "step_count": 1,
        "model_calls": 1,
    }
    row.update(over)
    return row


class TestOracleSelectionIsLabelled:
    """NF-02. `best-of-k` picks the attempt the grader passed — a verdict no real system sees at
    submission time — so its `success_rate` is identical to `pass_at_k` and its
    `selection_hit_rate` is identically 1.0 wherever defined. `configs/runs/passk.yaml` ships it
    and `configs/runs/ablation-matrix.yaml` McNemars that row against three honest ones."""

    def test_the_strategy_is_named_for_what_it_does(self):
        assert selection_is_oracle("best-of-k") is True
        assert selection_is_oracle("first-submit") is False

    def test_the_folded_row_carries_the_label(self):
        row = _aggregate_attempts([_attempt(False), _attempt(True)], strategy="best-of-k")
        assert row["passed"] is True
        assert row["selection_is_oracle"] is True

    def test_an_honest_run_is_not_labelled(self):
        row = _aggregate_attempts([_attempt(False), _attempt(True)], strategy="first-submit")
        assert row["selection_is_oracle"] is False

    def test_the_summary_and_the_report_both_say_so(self):
        rows = [_aggregate_attempts([_attempt(False), _attempt(True)], strategy="best-of-k")]
        summary = build_summary(
            run_id="r",
            run_manifest={"selection_strategy": "best-of-k", "selection_is_oracle": True},
            case_results=rows,
        )
        assert summary["selection_is_oracle"] is True
        report = render_report_md(summary, rows)
        assert "oracle" in report
        # The claim the label exists to block.
        assert summary["success_rate"] == summary["pass_at_k"]

    def test_an_oracle_row_is_marked_uncomparable_in_every_cross_run_column(self):
        rows = [
            {
                "experiment_name": "honest",
                "selection_is_oracle": False,
                "case_rows": [{"case_id": "a", "passed": True}, {"case_id": "b", "passed": False}],
            },
            {
                "experiment_name": "oracle",
                "selection_is_oracle": True,
                "selection_strategy": "best-of-k",
                "case_rows": [{"case_id": "a", "passed": True}, {"case_id": "b", "passed": True}],
            },
        ]
        pairwise = compare_runs_pairwise(rows, baseline="honest")
        assert pairwise[0]["comparable"] is False
        assert pairwise[0]["candidate_selection_is_oracle"] is True

        report = _render_ablation_report(
            [
                {**rows[0], "run_id": "r0", "success_rate": 0.5, "total_tokens": 1, "run_dir": "-"},
                {**rows[1], "run_id": "r1", "success_rate": 1.0, "total_tokens": 1, "run_dir": "-"},
            ],
            baseline="honest",
            pairwise=pairwise,
        )
        assert "oracle 上界" in report
        assert "不可并列比较" in report


class TestAMatrixRowThatMovesThreeKnobsSaysSo:
    """H9. The shipped `tool-loop-glm52` row changes the adapter, `max_wall_time_s` 300->600 and
    `case_workers` 4->2, because it points at a different run config. Every knob reaches
    `run_manifest.json`; nothing read them back to check the row varied one axis."""

    def test_a_multi_axis_row_is_named(self):
        rows = [
            {
                "experiment_name": "baseline",
                "manifest": {"agent_adapter": "openai_compat", "max_wall_time_s": 300},
            },
            {
                "experiment_name": "candidate",
                "manifest": {"agent_adapter": "tool_loop", "max_wall_time_s": 600},
            },
        ]
        axes = diff_axes_against_baseline(rows, baseline="baseline")
        assert sorted(axes["candidate"]) == ["agent_adapter", "max_wall_time_s"]

        report = _render_ablation_report(
            [
                {
                    **r,
                    "run_id": r["experiment_name"],
                    "success_rate": 0.5,
                    "total_tokens": 1,
                    "run_dir": "-",
                    "overview_row": {},
                }
                for r in rows
            ],
            baseline="baseline",
            axes=axes,
        )
        assert "不能归因到任何单一轴" in report

    def test_a_single_axis_row_is_not_flagged(self):
        rows = [
            {"experiment_name": "baseline", "manifest": {"main_model": "A", "max_steps": 40}},
            {"experiment_name": "candidate", "manifest": {"main_model": "B", "max_steps": 40}},
        ]
        assert diff_axes_against_baseline(rows, baseline="baseline")["candidate"] == ["main_model"]


class TestCostRungsAreComparedAcrossConfigs:
    """H10's cost-curve half. `token_amplification` answers "how many times the baseline's
    tokens", which is a different question from "at equal spend, which is ahead"; nothing
    compared the rungs across runs."""

    def test_the_report_carries_an_equal_budget_table(self):
        rows = [
            {
                "experiment_name": "cheap",
                "run_id": "r0",
                "success_rate": 0.5,
                "total_tokens": 10,
                "run_dir": "-",
                "cost_curve": [
                    {"budget_tokens": 100, "solved": 1, "success_rate": 0.5},
                    {"budget_tokens": 200, "solved": 1, "success_rate": 0.5},
                ],
            },
            {
                "experiment_name": "dear",
                "run_id": "r1",
                "success_rate": 0.9,
                "total_tokens": 90,
                "run_dir": "-",
                "cost_curve": [
                    {"budget_tokens": 200, "solved": 2, "success_rate": 0.9},
                ],
            },
        ]
        report = _render_ablation_report(rows, baseline="cheap")
        assert "等成本对比" in report
        assert "| 100 |" in report and "| 200 |" in report


class TestTheCostEstimateNamesItsRate:
    """M11 and RP-58's cost half. `total_cost` is tokens times a rate that is a built-in
    fallback unless `AIBENCH_USD_PER_MTOK*` is set — the published `total_cost: 5.303278` is
    exactly `(0.5+1.5)/2` applied to 5,303,278 tokens — and nothing recorded which it was."""

    def test_an_unconfigured_run_says_the_rate_is_invented(self, monkeypatch):
        for var in (
            "AIBENCH_USD_PER_MTOK",
            "AIBENCH_USD_PER_MTOK_INPUT",
            "AIBENCH_USD_PER_MTOK_OUTPUT",
        ):
            monkeypatch.delenv(var, raising=False)
        rate = resolved_usd_rate()
        assert rate["usd_per_mtok"] == 1.0
        assert "not a price" in rate["source"]

    def test_a_configured_rate_is_named(self, monkeypatch):
        monkeypatch.setenv("AIBENCH_USD_PER_MTOK", "2.5")
        rate = resolved_usd_rate()
        assert rate["usd_per_mtok"] == 2.5
        assert rate["source"] == "AIBENCH_USD_PER_MTOK"

    def test_the_summary_carries_the_rate_beside_the_cost(self, monkeypatch):
        monkeypatch.setenv("AIBENCH_USD_PER_MTOK", "2.0")
        summary = build_summary(
            run_id="r",
            run_manifest={},
            case_results=[_attempt(True, total_tokens=1_000_000)],
        )
        assert summary["total_cost"] == 2.0
        assert summary["cost_rate"]["source"] == "AIBENCH_USD_PER_MTOK"
