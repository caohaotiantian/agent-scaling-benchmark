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
from aibench.report import (
    build_summary,
    format_pct,
    render_report_md,
    render_summary_tables_json,
    resolved_usd_rate,
)
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

    @staticmethod
    def _rows() -> list[dict]:
        """Two configurations whose own quantiles land on different rungs.

        `cheap` never spends past 200 tokens; `dear` spends up to 900. Left to `report.py` each
        would publish a curve on its own x-axis, which is what made the union-of-rungs table
        report `cheap`'s 200-token rate in a 900-token column.
        """
        return [
            {
                "experiment_name": "cheap",
                "run_id": "r0",
                "success_rate": 0.5,
                "total_tokens": 300,
                "run_dir": "-",
                "case_rows": [
                    {"case_id": "a", "passed": True, "total_tokens": 100},
                    {"case_id": "b", "passed": False, "total_tokens": 200},
                ],
            },
            {
                "experiment_name": "dear",
                "run_id": "r1",
                "success_rate": 1.0,
                "total_tokens": 1400,
                "run_dir": "-",
                "case_rows": [
                    {"case_id": "a", "passed": True, "total_tokens": 500},
                    {"case_id": "b", "passed": True, "total_tokens": 900},
                ],
            },
        ]

    def test_the_report_carries_an_equal_budget_table(self):
        report = _render_ablation_report(self._rows(), baseline="cheap")
        assert "等成本对比" in report
        assert "cheap" in report and "dear" in report

    def test_no_curve_is_read_at_a_rung_it_never_measured(self):
        """`rate_at` step-held each run's last measured point, so a run whose highest rung was
        200 reported its 200-token rate in every column above it. At a 900-token budget `dear`
        has solved both cases and `cheap` still has one, and no cell may claim otherwise."""
        from aibench.ablation import _render_cost_rungs_md

        lines = _render_cost_rungs_md(self._rows())
        rungs = {
            int(ln.split("|")[1].strip()): [c.strip() for c in ln.split("|")[2:-1]]
            for ln in lines
            if ln.startswith("| ") and ln.split("|")[1].strip().isdigit()
        }
        assert rungs, lines
        top = max(rungs)
        assert top >= 900, f"the pooled rungs must reach the dearest case: {sorted(rungs)}"
        assert rungs[top] == ["50.0%", "100.0%"]
        # Every configuration is read at every rung — one shared x-axis, not a union of curves.
        assert all(len(cells) == 2 for cells in rungs.values())

    def test_without_per_case_rows_there_is_no_table(self):
        """The stored per-run curves are the incomparable ones. No table is the honest output;
        a table built from them would put each configuration on its own axis."""
        from aibench.ablation import _render_cost_rungs_md

        rows = [{k: v for k, v in r.items() if k != "case_rows"} for r in self._rows()]
        assert _render_cost_rungs_md(rows) == []


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


class TestARunThatMeasuredNothingRendersAsUnmeasured:
    """`success_rate` is None when every case died on infrastructure. Rendering it as 0.0% is
    the claim the None was introduced to avoid, and the headline was the one number in the
    report that did not go through `format_pct`."""

    def _rendered(self, infra: bool):
        rows = [
            _attempt(
                not infra,
                case_id=f"c{i}",
                infra_error=infra,
                agent_status="error" if infra else "completed",
                task_type="bugfix",
            )
            for i in range(3)
        ]
        summary = build_summary(
            run_id="r",
            run_manifest={
                "run_id": "r",
                "case_set": "s",
                "case_count": 3,
                "primary_metric_name": "task_success_rate",
            },
            case_results=rows,
            elapsed_wall_s=1.0,
        )
        return summary, render_report_md(summary, rows), render_summary_tables_json(summary)

    def _overview_row(self, md: str) -> list[str]:
        """The single-row table at the top of the report — the first number anyone quotes."""
        body = md.split("## 项目效果综述表（单行）", 1)[1].split("\n## ", 1)[0]
        rows = [ln for ln in body.splitlines() if ln.startswith("|")]
        return [c.strip() for c in rows[2].strip().strip("|").split("|")]  # header, ---, data

    def test_the_headline_says_unmeasured(self):
        summary, md, tables = self._rendered(infra=True)
        assert summary["success_rate"] is None
        assert "| 主指标值 | - |" in md
        assert "| 成功率 | - |" in md
        # The single-row table renders the same metric from the same variable, and had no test.
        assert self._overview_row(md)[6] == "-"
        assert tables["overview_row"]["主指标值"] == "-"
        # `general_row` already reported the raw rate under 成功率; 主指标值 now agrees with it.
        assert tables["general_row"]["主指标值"] is None
        assert tables["general_row"]["成功率"] is None
        # `完成率` sat in the same dict claiming "0% of the cases reached grading". None did.
        assert summary["completion_rate"] is None
        assert tables["general_row"]["完成率"] is None

    def test_the_wilson_interval_is_a_dash_not_the_word_none(self):
        _, md, _ = self._rendered(infra=True)
        # `format_wilson_ci(0, 0)` is None, and the row printed it verbatim.
        assert "| 成功率 95% CI | - |" in md
        assert "| 成功率 95% CI | None |" not in md

    def test_a_run_that_measured_something_still_reports_it(self):
        summary, md, tables = self._rendered(infra=False)
        assert summary["success_rate"] == 1.0
        assert "| 主指标值 | 100.0% |" in md
        assert "| 成功率 | 100.0% |" in md
        assert tables["overview_row"]["主指标值"] == "100.0%"
        assert tables["general_row"]["主指标值"] == 1.0
        assert self._overview_row(md)[6] == "100.0%"

    def test_a_real_zero_is_still_reported_as_zero(self):
        """Every rendering point now routes through one predicate. `if not value` would read a
        genuine 0% — a run that attempted every case and solved none — as unmeasured, in the
        report, the tables, the ablation and the XLSX at once, with nothing to catch it."""
        rows = [
            _attempt(False, case_id=f"c{i}", infra_error=False, agent_status="completed")
            for i in range(3)
        ]
        summary = build_summary(
            run_id="r",
            run_manifest={"run_id": "r", "case_set": "s", "case_count": 3},
            case_results=rows,
            elapsed_wall_s=1.0,
        )
        assert summary["success_rate"] == 0.0
        assert summary["effective_case_count"] == 3
        md = render_report_md(summary, rows)
        assert "| 主指标值 | 0.0% |" in md
        assert "| 成功率 | 0.0% |" in md
        assert "-" not in self._overview_row(md)[6]
        tables = render_summary_tables_json(summary)
        assert tables["overview_row"]["主指标值"] == "0.0%"
        assert tables["general_row"]["主指标值"] == 0.0
        assert summary["completion_rate"] == 1.0  # they all reached grading; none of them passed
        assert format_pct(0.0) == "0.0%"
