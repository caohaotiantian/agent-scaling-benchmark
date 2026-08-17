"""Run identity, panel identity, and an estimator that measures the case rather than the outage.

Three defects, all of which made a shipped number mean something other than it claimed:

* every one of the 148 run manifests carried the literal ``aibench@0.1.0 / agent@1.0.0``, so an
  adapter fix worth 58 points left no trace in any artifact;
* ``anchor_fingerprint`` hashed three YAML files and nothing else, so it was byte-identical
  across that same fix and ``--reuse-from`` would have carried the old p_hat through it;
* ``point_biserial`` scored a case against a total containing it and treated a run that
  produced no row as a failure, which correlates the case with the outage. On the most recent
  calibration, 11 of 13 kept cases had incomplete attempts.
"""

from __future__ import annotations

import random
import subprocess

from aibench.calibrate import (
    INCOMPLETE_PANEL,
    SelectionPolicy,
    aggregate_calibration,
    anchor_fingerprint,
    load_anchor_panel,
    verdict_reasons,
)
from aibench.io_util import repo_root
from aibench.provenance import environment, git_revision, harness_digest
from aibench.stats import item_rest_correlation, point_biserial


def _row(case_id, passed, *, infra=False):
    return {"case_id": case_id, "passed": passed, "infra_error": infra}


class TestRunIdentity:
    def test_code_version_is_a_revision_not_a_literal(self):
        """Compared against git itself: `env == git_revision()` would pass on any constant."""
        actual = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=repo_root(),
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        assert environment()["code_version"].split("-")[0] == actual

    def test_dirty_worktree_is_declared(self):
        rev = git_revision()
        assert rev == "unknown-worktree" or len(rev.split("-")[0]) >= 7

    def test_environment_names_the_interpreter_and_the_harness(self):
        env = environment()
        for field in ("harness_digest", "venv_digest", "python_version", "platform"):
            assert env[field], f"{field} missing"

    def test_no_absolute_path_names_this_machine(self):
        """`python_executable` and `working_directory` were a home directory in all 148
        manifests on disk, and neither is something a reader can act on. What matters is which
        packages were importable, which `venv_digest` answers without naming anyone."""
        env = environment()
        assert "python_executable" not in env
        assert "working_directory" not in env
        assert not [k for k, v in env.items() if isinstance(v, str) and v.startswith("/Users/")]

    def test_the_api_key_never_enters_an_artifact(self):
        assert "api_key" not in environment()


class TestPanelIdentity:
    def _anchors(self):
        return load_anchor_panel(repo_root() / "configs/runs/anchor-panel.yaml")[0]

    def test_fingerprint_carries_its_version(self):
        assert anchor_fingerprint(self._anchors()).startswith("v2:")

    def test_editing_an_adapter_moves_the_harness_digest(self, tmp_path, monkeypatch):
        """Config files name the agent; they say nothing about how it is driven.

        The probe edit lands in a *copy* of `src/aibench`. Writing it into the checkout — which
        is what this test used to do — leaves the working tree modified for the length of the
        test, and any concurrent read of `git status` or of the same file sees the probe.
        """
        import shutil

        import aibench.provenance as provenance

        copy_root = tmp_path / "checkout"
        shutil.copytree(repo_root() / "src", copy_root / "src")
        monkeypatch.setattr(provenance, "repo_root", lambda: copy_root)

        harness_digest.cache_clear()
        before = harness_digest()
        adapter = copy_root / "src/aibench/agents/openai_compat.py"
        adapter.write_bytes(adapter.read_bytes() + b"\n# probe\n")
        harness_digest.cache_clear()
        assert harness_digest() != before, "an adapter edit must move the harness digest"
        harness_digest.cache_clear()

    def test_the_panel_fingerprint_carries_the_harness_digest(self, monkeypatch):
        """The digest is what makes the panel witness an adapter change, so the panel must
        actually consult it — hashing the three YAML files alone left the fingerprint
        byte-identical across a fix worth 58 points."""
        import aibench.provenance as provenance

        anchors = self._anchors()
        harness_digest.cache_clear()
        before = anchor_fingerprint(anchors)
        monkeypatch.setattr(provenance, "harness_digest", lambda _=None: "not-the-real-digest")
        assert anchor_fingerprint(anchors) != before
        harness_digest.cache_clear()


class TestItemRestCorrelation:
    def test_a_pure_noise_case_does_not_correlate_with_ability(self):
        """The uncorrected statistic centres near 1/sqrt(k) — 0.18 at k=31, above the threshold
        that exists to reject exactly such a case."""
        rng = random.Random(4)
        k, runs = 31, 9
        naive, corrected = [], []
        for _ in range(400):
            others = [[rng.random() < 0.5 for _ in range(runs)] for _ in range(k - 1)]
            item = [1.0 if rng.random() < 0.5 else 0.0 for _ in range(runs)]
            passes = [sum(1 for row in others if row[r]) + item[r] for r in range(runs)]
            if (n := point_biserial(item, passes)) is not None:
                naive.append(n)
            c = item_rest_correlation(item, passes, [k] * runs)
            if c is not None:
                corrected.append(c)
        assert sum(naive) / len(naive) > 0.10
        assert abs(sum(corrected) / len(corrected)) < 0.05

    def test_an_outage_is_not_weakness(self):
        """A run that lost two thirds of its rows solved a third as many cases, for a reason
        that has nothing to do with capability.

        The shape is taken from `runs/calibration_20260809_231654`: pass counts
        ``[26, 7, 24]`` against rows actually measured ``[30, 9, 31]``. A case that fails on
        the depleted run reads, in counts, as failing exactly where ability is lowest — a
        strong positive correlation with the outage. By rate the middle run is the *strongest*
        of the three (0.875), and the correlation goes away.
        """
        item = [1.0, 0.0, 1.0]
        passes = [26.0, 7.0, 24.0]
        measured = [30, 9, 31]
        by_count = point_biserial(item, [p - i for i, p in zip(item, passes, strict=True)])
        by_rate = item_rest_correlation(item, passes, measured)
        assert by_count is not None and by_rate is not None
        assert by_count > 0.9, "counts should read the outage as a near-perfect discriminator"
        assert by_rate < 0.0, "by rate the case tracks nothing"

    def test_a_genuinely_discriminating_case_still_scores(self):
        item = [0.0, 0.0, 1.0, 1.0]
        assert (item_rest_correlation(item, [1.0, 2.0, 8.0, 9.0], [10] * 4) or 0) > 0.9

    def test_mismatched_lengths_are_refused(self):
        assert item_rest_correlation([1.0, 0.0], [1.0], [10, 10]) is None

    def test_a_run_measuring_only_this_case_has_no_rest(self):
        assert item_rest_correlation([1.0, 0.0], [1.0, 0.0], [1, 5]) is None


class TestIncompletePanelIsNotAQualityVerdict:
    def test_a_partly_measured_case_is_blocked_before_any_threshold(self):
        reasons = verdict_reasons(
            0.5, 0.9, SelectionPolicy(), anchors_measured=2, anchors_expected=3
        )
        assert len(reasons) == 1
        assert reasons[0].startswith(INCOMPLETE_PANEL)

    def test_the_reason_says_to_re_run(self):
        (reason,) = verdict_reasons(
            0.5, 0.9, SelectionPolicy(), anchors_measured=1, anchors_expected=3
        )
        assert "re-run" in reason

    def test_full_coverage_falls_through_to_the_quality_thresholds(self):
        assert (
            verdict_reasons(0.5, 0.9, SelectionPolicy(), anchors_measured=3, anchors_expected=3)
            == []
        )
        assert verdict_reasons(
            1.0, 0.9, SelectionPolicy(), anchors_measured=3, anchors_expected=3
        ) == ["too_easy(p=1.00>0.9)"]

    def test_unknown_coverage_does_not_block(self):
        """Reused rows from before this field existed must not all become incomplete."""
        assert verdict_reasons(0.5, 0.9, SelectionPolicy()) == []


class TestMissingRunsAreExcludedNotZeroed:
    def test_a_case_absent_from_one_anchor_is_not_correlated_with_the_outage(self):
        """`rev-4646d93ae250add0` scored r_pb 0.996 on attempts 6 of 9 while passing every one.

        Zero-filling the run it was missing from made it look failed exactly where that run also
        scored low, which is a correlation with the gap rather than with ability.
        """
        runs = [
            {"anchor": "weak", "rows": [_row("a", True), _row("b", False)]},
            {"anchor": "mid", "rows": [_row("a", True), _row("b", False)]},
            {"anchor": "strong", "rows": [_row("b", True)]},  # "a" never ran here
        ]
        report = aggregate_calibration(runs)
        a = next(c for c in report["cases"] if c["case_id"] == "a")
        assert a["attempts"] == 2
        assert a["p_hat"] == 1.0
        assert any(r.startswith(INCOMPLETE_PANEL) for r in a["reasons"])
        assert not a["keep"]

    def test_coverage_is_reported_alongside_the_verdicts(self):
        runs = [
            {"anchor": "weak", "rows": [_row("a", True), _row("b", False, infra=True)]},
            {"anchor": "strong", "rows": [_row("a", False), _row("b", True)]},
        ]
        report = aggregate_calibration(runs)
        assert report["run_count"] == 2
        assert report["rows_dropped_by_anchor"] == {"weak": 1}
        assert report["incomplete_panel_count"] >= 1
