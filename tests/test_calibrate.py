"""Calibration item statistics and the selection policy."""

import json

import pytest

from aibench.calibrate import (
    AnchorSpec,
    SelectionPolicy,
    aggregate_calibration,
    load_anchor_panel,
    render_calibration_md,
    select_cases,
)
from aibench.io_util import repo_root, write_json
from aibench.stats import mcnemar_test, paired_outcomes, point_biserial


def _run(anchor: str, outcomes: dict[str, bool], *, tier: str = "T3") -> dict:
    return {
        "anchor": anchor,
        "rows": [
            {"case_id": cid, "passed": ok, "infra_error": False, "tier": tier}
            for cid, ok in outcomes.items()
        ],
    }


def _panel(
    weak: dict[str, bool], mid: dict[str, bool], strong: dict[str, bool], *, repeats: int = 2
) -> list[dict]:
    runs = []
    for _ in range(repeats):
        runs.extend([_run("weak", weak), _run("mid", mid), _run("strong", strong)])
    return runs


def test_a_case_everyone_solves_is_dropped_as_too_easy():
    runs = _panel({"easy": True}, {"easy": True}, {"easy": True})
    rep = aggregate_calibration(runs)
    (case,) = rep["cases"]
    assert case["p_hat"] == 1.0
    assert case["keep"] is False
    assert any("too_easy" in r for r in case["reasons"])


def test_a_case_nobody_solves_is_flagged_for_review_not_kept():
    runs = _panel({"impossible": False}, {"impossible": False}, {"impossible": False})
    (case,) = aggregate_calibration(runs)["cases"]
    assert case["keep"] is False
    assert any("unsolved_by_all" in r for r in case["reasons"])


def test_a_case_that_separates_the_panel_is_kept():
    runs = _panel(
        {"disc": False, "filler": True},
        {"disc": False, "filler": True},
        {"disc": True, "filler": True},
    )
    rep = aggregate_calibration(runs)
    disc = next(c for c in rep["cases"] if c["case_id"] == "disc")
    assert disc["by_anchor"] == {"weak": 0.0, "mid": 0.0, "strong": 1.0}
    assert disc["spread"] == 1.0
    assert disc["keep"] is True
    assert rep["kept_count"] == 1  # 'filler' is dropped as too easy


def test_an_uncorrelated_case_is_dropped_as_noise():
    """Mid-range p_hat is not enough: the outcome must track overall ability."""
    runs = _panel(
        {"noise": True, "a": False, "b": False},
        {"noise": False, "a": True, "b": False},
        {"noise": False, "a": True, "b": True},
    )
    noise = next(c for c in aggregate_calibration(runs)["cases"] if c["case_id"] == "noise")
    assert 0.05 <= noise["p_hat"] <= 0.9
    assert noise["point_biserial"] is not None and noise["point_biserial"] < 0.15
    assert noise["keep"] is False
    assert any("no_discrimination" in r for r in noise["reasons"])


def test_instability_within_one_anchor_is_reported_as_flaky():
    runs = [_run("weak", {"c": True}), _run("weak", {"c": False}), _run("mid", {"c": True})]
    (case,) = aggregate_calibration(runs)["cases"]
    assert case["flaky"] is True


def test_infra_errors_are_not_evidence_about_the_case():
    runs = [
        {"anchor": "weak", "rows": [{"case_id": "c", "passed": False, "infra_error": True}]},
        {"anchor": "mid", "rows": [{"case_id": "c", "passed": True, "infra_error": False}]},
    ]
    (case,) = aggregate_calibration(runs)["cases"]
    assert case["attempts"] == 1
    assert case["p_hat"] == 1.0


def test_policy_thresholds_are_configurable():
    runs = _panel({"c": True}, {"c": True}, {"c": False})
    strict = aggregate_calibration(runs, policy=SelectionPolicy(p_max=0.5))
    assert strict["cases"][0]["keep"] is False
    loose = aggregate_calibration(runs, policy=SelectionPolicy(p_max=0.95, min_rpb=-1.0))
    assert loose["cases"][0]["keep"] is True


def test_distribution_summary_reports_both_all_and_kept():
    runs = _panel(
        {"easy": True, "hard": False},
        {"easy": True, "hard": False},
        {"easy": True, "hard": True},
    )
    rep = aggregate_calibration(runs)
    assert sum(rep["p_hat_distribution"].values()) == 2
    assert rep["kept_p_hat_distribution"]["0.2-0.5"] == 1
    assert rep["tier_distribution"] == {"T3": 1}


def test_calibration_report_renders():
    rep = aggregate_calibration(_panel({"c": False}, {"c": False}, {"c": True}))
    rep["case_set"] = "auto-v0"
    md = render_calibration_md(rep)
    assert "# Case Calibration" in md
    assert "| c |" in md


def test_select_copies_the_kept_cases_best_discriminators_first(tmp_path, monkeypatch):
    src = repo_root() / "benchmarks/ai_coding/cases/_test_cal_src"
    dest = repo_root() / "benchmarks/ai_coding/cases/_test_cal_dest"
    src.mkdir(parents=True, exist_ok=True)
    try:
        for cid in ("keeper", "giveaway"):
            write_json(
                src / f"{cid}.json",
                {
                    "case_id": cid,
                    "schema_version": "0.1",
                    "task_type": "bugfix",
                    "language": "python",
                    "prompt": "p",
                    "context": {"files": [{"path": "a.py", "content": "x=1\n"}]},
                    "grader": {"mode": "script", "command": "python -m pytest -q"},
                    "metadata": {},
                },
            )
        calibration = aggregate_calibration(
            _panel(
                {"keeper": False, "giveaway": True},
                {"keeper": False, "giveaway": True},
                {"keeper": True, "giveaway": True},
            )
        )
        report = select_cases(calibration, source_set=src.name, dest_set=dest.name)
        assert report["selected"] == ["keeper"]
        written = json.loads((dest / "keeper.json").read_text(encoding="utf-8"))
        assert written["metadata"]["calibration"]["spread"] == 1.0
    finally:
        for d in (src, dest):
            if d.exists():
                for f in d.glob("*.json"):
                    f.unlink()
                d.rmdir()


def test_anchor_panel_config_spans_both_axes():
    anchors, _ = load_anchor_panel(repo_root() / "configs/runs/anchor-panel.yaml")
    assert len(anchors) >= 3
    assert len({a.model_config for a in anchors}) >= 2, "panel must vary the model"
    assert len({a.agent_config for a in anchors}) >= 2, "panel must vary the agent"
    assert all(isinstance(a, AnchorSpec) for a in anchors)


def test_mcnemar_detects_a_difference_wilson_intervals_would_miss():
    """56/64 vs 62/64 overlap on independent CIs but are paired-significant."""
    base = [{"case_id": f"c{i}", "passed": i < 56} for i in range(64)]
    cand = [{"case_id": f"c{i}", "passed": i < 62} for i in range(64)]
    both, only_base, only_cand, neither = paired_outcomes(base, cand)
    assert (both, only_base, only_cand, neither) == (56, 0, 6, 2)
    result = mcnemar_test(only_base, only_cand)
    assert result["discordant"] == 6
    assert result["significant"] is True


def test_mcnemar_is_symmetric_and_null_safe():
    assert mcnemar_test(0, 0)["p_value"] == 1.0
    assert mcnemar_test(3, 3)["significant"] is False
    assert mcnemar_test(6, 0)["p_value"] == mcnemar_test(0, 6)["p_value"]


def test_paired_outcomes_only_uses_shared_non_infra_cases():
    a = [{"case_id": "x", "passed": True}, {"case_id": "only_a", "passed": True}]
    b = [{"case_id": "x", "passed": False}, {"case_id": "err", "passed": True, "infra_error": True}]
    assert paired_outcomes(a, b) == (0, 1, 0, 0)


def test_point_biserial_edges():
    assert point_biserial([1.0, 0.0], [2.0, 1.0]) == 1.0
    assert point_biserial([1.0, 1.0], [2.0, 1.0]) is None  # no variance in the item
    assert point_biserial([1.0], [1.0]) is None


def _cal(case_id, tier, spread, rpb=0.9):
    return {"case_id": case_id, "tier": tier, "spread": spread, "point_biserial": rpb, "keep": True}


def test_parse_tier_quota():
    from aibench.calibrate import parse_tier_quota

    assert parse_tier_quota("T2=0.5,T3=0.5") == {"T2": 0.5, "T3": 0.5}
    assert parse_tier_quota(None) == {}
    assert parse_tier_quota("") == {}
    with pytest.raises(ValueError):
        parse_tier_quota("T2")


def test_without_a_quota_selection_can_collapse_into_one_tier():
    from aibench.calibrate import apply_tier_quota

    keep = [_cal(f"t2-{i}", "T2", 1.0) for i in range(4)] + [_cal("t3-a", "T3", 0.4)]
    picked = apply_tier_quota(keep, quota={}, max_cases=4)
    assert {c["tier"] for c in picked} == {"T2"}


def test_a_quota_keeps_the_coverage_the_tiers_were_built_for():
    from aibench.calibrate import apply_tier_quota

    keep = [_cal(f"t2-{i}", "T2", 1.0) for i in range(4)] + [
        _cal(f"t3-{i}", "T3", 0.4) for i in range(4)
    ]
    picked = apply_tier_quota(keep, quota={"T2": 0.5, "T3": 0.5}, max_cases=4)
    counts = {}
    for c in picked:
        counts[c["tier"]] = counts.get(c["tier"], 0) + 1
    assert counts == {"T2": 2, "T3": 2}


def test_an_underfilled_quota_is_topped_up_rather_than_returning_short():
    from aibench.calibrate import apply_tier_quota

    keep = [_cal(f"t2-{i}", "T2", 1.0) for i in range(5)] + [_cal("t4-a", "T4", 0.9)]
    picked = apply_tier_quota(keep, quota={"T4": 0.5, "T2": 0.5}, max_cases=4)
    assert len(picked) == 4
    assert sum(1 for c in picked if c["tier"] == "T4") == 1


def test_quota_selection_still_prefers_the_better_discriminators_within_a_tier():
    from aibench.calibrate import apply_tier_quota

    keep = [_cal("weak", "T2", 0.2), _cal("strong", "T2", 1.0)]
    picked = apply_tier_quota(keep, quota={"T2": 1.0}, max_cases=1)
    assert [c["case_id"] for c in picked] == ["strong"]


def test_anchor_fingerprint_tracks_config_contents_not_just_paths(tmp_path, monkeypatch):
    """Swapping the model inside a referenced YAML changes what the anchors mean while every
    path stays identical, so a p_hat measured against the old panel must not stay trusted."""
    from aibench.calibrate import AnchorSpec, anchor_fingerprint

    panel = [
        AnchorSpec(
            name="a",
            agent_config="configs/agents/openai_compat.yaml",
            model_config="configs/models/glm52.yaml",
        )
    ]
    before = anchor_fingerprint(panel)
    assert before == anchor_fingerprint(panel), "must be stable for unchanged configs"

    renamed = [
        AnchorSpec(
            name="a",
            agent_config="configs/agents/openai_compat.yaml",
            model_config="configs/models/glm51.yaml",
        )
    ]
    assert anchor_fingerprint(renamed) != before


def test_a_changed_panel_invalidates_every_previous_result():
    from aibench.calibrate import plan_calibration

    previous = {"anchor_fingerprint": "old", "cases": [{"case_id": "c1", "fingerprint": "f1"}]}
    todo, reused = plan_calibration(["c1"], {"c1": "f1"}, previous, panel="new")
    assert todo == ["c1"] and reused == []


def test_unchanged_cases_are_reused_and_changed_ones_re_run():
    from aibench.calibrate import plan_calibration
    from aibench.validity import FINGERPRINT_VERSION

    v = FINGERPRINT_VERSION
    previous = {
        "anchor_fingerprint": "p1",
        "cases": [
            {"case_id": "same", "fingerprint": f"{v}:f-same"},
            {"case_id": "edited", "fingerprint": f"{v}:f-old"},
        ],
    }
    todo, reused = plan_calibration(
        ["same", "edited", "brand-new"],
        {"same": f"{v}:f-same", "edited": f"{v}:f-new", "brand-new": f"{v}:f-x"},
        previous,
        panel="p1",
    )
    assert todo == ["edited", "brand-new"]
    assert [c["case_id"] for c in reused] == ["same"]


def test_no_previous_calibration_means_everything_runs():
    from aibench.calibrate import plan_calibration

    todo, reused = plan_calibration(["a", "b"], {"a": "1", "b": "2"}, None, panel="p")
    assert todo == ["a", "b"] and reused == []


def test_merging_reused_results_recomputes_the_set_level_distributions():
    from aibench.calibrate import _merge_reused

    fresh = aggregate_calibration(_panel({"new": False}, {"new": False}, {"new": True}))
    reused = [
        {"case_id": "old", "tier": "T2", "p_hat": 1.0, "keep": False, "reasons": ["too_easy"]}
    ]
    merged = _merge_reused(fresh, reused, policy=None)
    assert merged["total_cases"] == 2
    assert merged["kept_count"] == 1
    assert merged["p_hat_distribution"]["0.8-1.0"] == 1
    assert [c["case_id"] for c in merged["cases"]] == ["new", "old"]
