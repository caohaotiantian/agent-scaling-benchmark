"""A panel may only calibrate tiers whose capabilities its members can exercise."""

import pytest

from aibench.calibrate import AnchorSpec, calibrate_case_set, load_anchor_panel, unfit_anchors
from aibench.io_util import repo_root
from aibench.models import AgentConfig
from aibench.tiers import TIER_SPECS


def test_a_single_turn_agent_declares_it_cannot_retrieve():
    """It pastes every file into the prompt, so there is nothing to search for. Measured: on
    composed retrieval cases the weak single-turn anchor scored 24pp *higher* than on the same
    cases uncomposed — the distractors were free working code, not an obstacle."""
    from aibench.io_util import load_yaml

    single = AgentConfig.from_dict(load_yaml(repo_root() / "configs/agents/openai_compat.yaml"))
    loop = AgentConfig.from_dict(load_yaml(repo_root() / "configs/agents/tool_loop.yaml"))
    assert "A2" not in single.capability_axes
    assert "A3" not in single.capability_axes
    assert "A2" in loop.capability_axes and "A3" in loop.capability_axes


def test_the_default_panel_is_unfit_for_retrieval_tiers():
    panel, _ = load_anchor_panel(repo_root() / "configs/runs/anchor-panel.yaml")
    assert unfit_anchors(panel, {"T2", "T3"}) == []
    unfit = unfit_anchors(panel, {"T4"})
    assert {name for name, _, _ in unfit} == {"weak-single-turn", "mid-single-turn"}
    assert all(missing == ["A2"] for _, _, missing in unfit)


def test_the_retrieval_panel_can_measure_retrieval():
    panel, _ = load_anchor_panel(repo_root() / "configs/runs/anchor-panel-retrieval.yaml")
    assert len(panel) >= 2, "one member always yields spread 0 for want of anyone to differ from"
    assert unfit_anchors(panel, {"T4"}) == []


def test_the_retrieval_panel_admits_it_cannot_measure_t5():
    """Its frugal members have no shell, so they cannot iterate on test output (A3). Saying so
    is the point: silently scoring T5 on them would report a capability nobody exercised."""
    panel, _ = load_anchor_panel(repo_root() / "configs/runs/anchor-panel-retrieval.yaml")
    unfit = unfit_anchors(panel, {"T5"})
    assert unfit, "T5 needs A3 and the frugal members lack bash"
    assert all(missing == ["A3"] for _, _, missing in unfit)


def test_an_undeclared_agent_is_treated_as_unknown_not_unfit():
    """Older configs carry no capability_axes; they must keep working."""
    anchor = AnchorSpec(
        name="legacy",
        agent_config="tests/fixtures/configs/agents/mock.yaml",
        model_config="tests/fixtures/configs/models/mock-model.yaml",
    )
    assert unfit_anchors([anchor], {"T4", "T5"}) == []


def test_calibrating_with_an_unfit_panel_is_refused(tmp_path):
    # `_t4fixture` is a synthetic retrieval-tier case set under tests/fixtures. The generated
    # sets are gitignored, so pointing this at one made the test depend on whether the machine
    # happened to have built it.
    panel, _ = load_anchor_panel(repo_root() / "configs/runs/anchor-panel.yaml")
    with pytest.raises(ValueError, match="cannot measure the tiers"):
        calibrate_case_set("_t4fixture", panel, repeats=1, output_root=tmp_path)


def test_every_tier_axis_is_a_known_axis():
    from aibench.tiers import AXES

    for spec in TIER_SPECS.values():
        for axis in spec.axes:
            assert axis in AXES, f"{spec.tier} requires unknown axis {axis}"
