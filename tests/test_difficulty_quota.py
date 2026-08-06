"""Shaping a case set by measured difficulty.

Thresholds cannot produce a distribution. `SelectionPolicy` drops the unusable, and everything
between p_min and p_max is then ranked purely by discrimination — which is why a selected set
still ran 39.3% easy: the whole 0.8-0.9 slice survives. A quota is what gives the set a shape.

The shortfall is reported rather than back-filled from a neighbouring band, because a set that
missed its target is a fact about the pool, and hiding it would let the next reader believe a
shape was achieved that never was.
"""

import pytest

from aibench.calibrate import apply_difficulty_quota, difficulty_band, select_cases


def _case(cid, p_hat, spread=0.5, tier="T3"):
    return {
        "case_id": cid,
        "p_hat": p_hat,
        "spread": spread,
        "point_biserial": 0.4,
        "tier": tier,
        "keep": True,
    }


class TestBands:
    @pytest.mark.parametrize(
        ("p_hat", "band"),
        [
            (0.0, "hard"),
            (0.19, "hard"),
            (0.2, "mid"),
            (0.5, "mid"),
            (0.8, "mid"),
            (0.81, "easy"),
            (1.0, "easy"),
        ],
    )
    def test_boundaries(self, p_hat, band):
        assert difficulty_band(p_hat) == band

    def test_an_unmeasured_case_is_not_called_easy_or_hard(self):
        # Absent evidence must not be read as either extreme.
        assert difficulty_band(None) == "mid"


class TestQuota:
    def test_shares_are_honoured_when_the_pool_allows(self):
        pool = (
            [_case(f"e{i}", 0.95) for i in range(10)]
            + [_case(f"m{i}", 0.5) for i in range(20)]
            + [_case(f"h{i}", 0.1) for i in range(10)]
        )
        picked, rep = apply_difficulty_quota(
            pool, quota={"easy": 0.15, "mid": 0.70, "hard": 0.15}, max_cases=20
        )
        assert rep["bands"]["easy"]["got"] == 3
        assert rep["bands"]["mid"]["got"] == 14
        assert rep["bands"]["hard"]["got"] == 3
        assert rep["shortfall"] == {}
        assert len(picked) == 20

    def test_a_short_band_is_reported_not_back_filled(self):
        # The real pool has exactly this shape: plenty of easy and mid, almost no hard.
        pool = [_case(f"e{i}", 0.95) for i in range(10)] + [_case(f"m{i}", 0.5) for i in range(20)]
        picked, rep = apply_difficulty_quota(
            pool, quota={"easy": 0.15, "mid": 0.70, "hard": 0.15}, max_cases=20
        )
        assert rep["shortfall"] == {"hard": 3}
        assert rep["bands"]["hard"] == {"wanted": 3, "got": 0, "pool": 0}
        # The missing three are NOT made up from easy or mid.
        assert len(picked) == 17
        assert all(difficulty_band(c["p_hat"]) != "hard" for c in picked)

    def test_actual_shares_are_reported_against_the_target(self):
        pool = [_case(f"e{i}", 0.95) for i in range(10)] + [_case(f"m{i}", 0.5) for i in range(20)]
        _, rep = apply_difficulty_quota(
            pool, quota={"easy": 0.15, "mid": 0.70, "hard": 0.15}, max_cases=20
        )
        assert rep["target"] == {"easy": 0.15, "mid": 0.70, "hard": 0.15}
        assert rep["actual_shares"]["hard"] == 0.0
        assert rep["actual_shares"]["mid"] > 0.7

    def test_the_best_discriminators_win_within_a_band(self):
        pool = [_case("weak", 0.5, spread=0.1), _case("strong", 0.5, spread=0.9)]
        picked, _ = apply_difficulty_quota(pool, quota={"mid": 1.0}, max_cases=1)
        assert [c["case_id"] for c in picked] == ["strong"]

    def test_no_quota_leaves_the_selection_untouched(self):
        pool = [_case(f"m{i}", 0.5) for i in range(5)]
        picked, rep = apply_difficulty_quota(pool, quota={}, max_cases=3)
        assert len(picked) == 3
        assert rep == {}


class TestTierWithinBand:
    def test_a_tier_quota_spreads_the_picks_inside_each_band(self):
        pool = [_case(f"a{i}", 0.5, tier="T2") for i in range(10)] + [
            _case(f"b{i}", 0.5, tier="T3") for i in range(10)
        ]
        picked, _ = apply_difficulty_quota(
            pool, quota={"mid": 1.0}, max_cases=10, tier_quota={"T2": 0.5, "T3": 0.5}
        )
        tiers = [c["tier"] for c in picked]
        assert tiers.count("T2") == 5
        assert tiers.count("T3") == 5

    def test_without_a_tier_quota_the_band_ranks_purely_by_discrimination(self):
        pool = [_case("t2", 0.5, spread=0.9, tier="T2"), _case("t3", 0.5, spread=0.1, tier="T3")]
        picked, _ = apply_difficulty_quota(pool, quota={"mid": 1.0}, max_cases=1)
        assert picked[0]["tier"] == "T2"


class TestSelectCasesReporting:
    def test_the_report_carries_the_achieved_distribution(self):
        cal = {
            "cases": [_case(f"m{i}", 0.5) for i in range(4)] + [_case("e0", 0.95)],
        }
        rep = select_cases(
            cal,
            source_set="auto-v0",
            dest_set="_quota_test",
            max_cases=4,
            difficulty_quota={"easy": 0.25, "mid": 0.75},
            dry_run=True,
        )
        assert rep["difficulty_quota"]["bands"]["mid"]["got"] == 3
        assert rep["difficulty_quota"]["bands"]["easy"]["got"] == 1
        assert "difficulty_distribution" in rep
