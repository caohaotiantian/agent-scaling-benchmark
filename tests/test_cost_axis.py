"""Cost axis: budget rungs, the success-vs-budget curve, and token amplification."""

from aibench.ablation import _render_ablation_report, attach_token_amplification
from aibench.report import build_summary
from aibench.stats import budget_quantiles, cost_curve


def _row(case_id, passed, tokens, *, infra=False):
    return {"case_id": case_id, "passed": passed, "total_tokens": tokens, "infra_error": infra}


def test_budget_rungs_come_from_the_observed_spend():
    rows = [_row(f"c{i}", True, t) for i, t in enumerate([100, 200, 300, 400])]
    assert budget_quantiles(rows, fractions=(0.25, 0.5, 1.0)) == [100, 200, 400]


def test_budget_rungs_ignore_infra_errors_and_empty_input():
    assert budget_quantiles([_row("c", False, 999, infra=True)]) == []
    assert budget_quantiles([]) == []


def test_curve_counts_only_cases_solved_within_the_budget():
    rows = [_row("cheap", True, 100), _row("dear", True, 900), _row("failed", False, 50)]
    curve = cost_curve(rows, budgets=[100, 900])
    assert curve[0] == {"budget_tokens": 100, "solved": 1, "success_rate": 1 / 3}
    assert curve[1] == {"budget_tokens": 900, "solved": 2, "success_rate": 2 / 3}


def test_curve_separates_two_configs_at_equal_accuracy():
    """The whole point: same success rate, different cost, and the curve shows it."""
    budgets = [100, 500]
    frugal = [_row("a", True, 90), _row("b", True, 90)]
    wasteful = [_row("a", True, 400), _row("b", True, 400)]
    assert cost_curve(frugal, budgets=budgets)[0]["success_rate"] == 1.0
    assert cost_curve(wasteful, budgets=budgets)[0]["success_rate"] == 0.0
    assert cost_curve(wasteful, budgets=budgets)[1]["success_rate"] == 1.0


def test_summary_carries_the_curve():
    rows = [_row("a", True, 100), _row("b", False, 800)]
    s = build_summary(run_id="r", run_manifest={}, case_results=rows)
    assert s["cost_curve"]
    assert all("budget_tokens" in p for p in s["cost_curve"])


def test_token_amplification_is_relative_to_the_baseline():
    rows = [
        {"experiment_name": "base", "total_tokens": 1000, "success_rate": 0.5},
        {"experiment_name": "passk", "total_tokens": 5000, "success_rate": 0.7},
        {"experiment_name": "zero", "total_tokens": 0, "success_rate": 0.0},
    ]
    attach_token_amplification(rows, baseline="base")
    assert rows[0]["token_amplification"] == 1.0
    assert rows[1]["token_amplification"] == 5.0
    assert rows[2]["token_amplification"] == 0.0


def test_token_amplification_is_none_without_a_usable_baseline():
    rows = [{"experiment_name": "a", "total_tokens": 10, "success_rate": 1.0}]
    attach_token_amplification(rows, baseline="missing")
    assert rows[0]["token_amplification"] is None

    zero_base = [{"experiment_name": "b", "total_tokens": 0, "success_rate": 0.0}]
    attach_token_amplification(zero_base, baseline="b")
    assert zero_base[0]["token_amplification"] is None


def test_ablation_report_shows_the_cost_trade():
    rows = [
        {
            "experiment_name": "base",
            "run_id": "r1",
            "success_rate": 0.5,
            "total_tokens": 1000,
            "total_cost": 0.001,
            "run_dir": "/tmp/a",
            "effective_case_count": 2,
            "infra_error_count": 0,
            "token_amplification": 1.0,
            "pass_at_1": 0.5,
            "pass_at_k": 0.5,
            "overview_row": {},
        },
        {
            "experiment_name": "passk",
            "run_id": "r2",
            "success_rate": 1.0,
            "total_tokens": 5000,
            "total_cost": 0.005,
            "run_dir": "/tmp/b",
            "effective_case_count": 2,
            "infra_error_count": 0,
            "token_amplification": 5.0,
            "pass_at_1": 0.5,
            "pass_at_k": 1.0,
            "overview_row": {},
        },
    ]
    md = _render_ablation_report(rows, baseline="base")
    assert "采样扩展与成本" in md
    assert "5.00x" in md
