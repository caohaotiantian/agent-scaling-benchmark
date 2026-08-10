"""Sample-size planning for the paired comparison."""

import math

import pytest

from aibench.stats import mcnemar_sample_size, normal_quantile, observed_discordance


def test_normal_quantile_matches_known_values():
    assert math.isclose(normal_quantile(0.975), 1.959964, abs_tol=1e-5)
    assert math.isclose(normal_quantile(0.8), 0.841621, abs_tol=1e-5)
    assert math.isclose(normal_quantile(0.5), 0.0, abs_tol=1e-9)
    assert math.isclose(normal_quantile(0.025), -1.959964, abs_tol=1e-5)
    # Tail branches of the piecewise approximation.
    assert normal_quantile(0.001) < -3.0
    assert normal_quantile(0.999) > 3.0
    for bad in (0.0, 1.0, -0.1, 1.1):
        with pytest.raises(ValueError):
            normal_quantile(bad)


def test_smaller_effects_need_more_cases():
    big = mcnemar_sample_size(delta=0.20, discordance=0.40)
    small = mcnemar_sample_size(delta=0.05, discordance=0.40)
    assert small["required_cases"] > big["required_cases"]


def test_noisier_disagreement_needs_more_cases():
    """At a fixed delta, higher discordance means the disagreement is less one-sided.

    delta=10pp out of 15% discordance is a lopsided split (most disagreements favour one
    side) and settles quickly; the same 10pp out of 50% discordance is mostly cancelling
    noise and needs far more cases.
    """
    lopsided = mcnemar_sample_size(delta=0.10, discordance=0.15)
    noisy = mcnemar_sample_size(delta=0.10, discordance=0.50)
    assert noisy["required_cases"] > lopsided["required_cases"]


def test_discordance_must_exceed_delta():
    """Two runs cannot differ by more than they disagree, so the formula refuses it."""
    with pytest.raises(ValueError, match="must exceed"):
        mcnemar_sample_size(delta=0.20, discordance=0.20)


def test_higher_power_needs_more_cases():
    assert (
        mcnemar_sample_size(delta=0.1, discordance=0.3, power=0.95)["required_cases"]
        > mcnemar_sample_size(delta=0.1, discordance=0.3, power=0.8)["required_cases"]
    )


def test_expected_discordant_pairs_are_reported():
    plan = mcnemar_sample_size(delta=0.1, discordance=0.4)
    assert plan["expected_discordant_pairs"] == math.ceil(plan["required_cases"] * 0.4)


def test_impossible_inputs_are_refused():
    with pytest.raises(ValueError):
        mcnemar_sample_size(delta=0.5, discordance=0.2)  # cannot differ more than they disagree
    with pytest.raises(ValueError):
        mcnemar_sample_size(delta=0.0, discordance=0.3)
    with pytest.raises(ValueError):
        mcnemar_sample_size(delta=0.1, discordance=0.0)


def test_discordance_can_be_measured_from_a_previous_ablation():
    pairwise = [
        {"discordant": 6, "both_passed": 56, "neither": 2},
        {"discordant": 10, "both_passed": 50, "neither": 4},
    ]
    assert math.isclose(observed_discordance(pairwise), 16 / 128)
    assert observed_discordance([]) is None
    assert observed_discordance([{"discordant": 0, "both_passed": 0, "neither": 0}]) is None
