"""A case's identity must cover what a solver actually sees.

Hashing the prompt and the file paths alone meant a case whose stub and reference solution
had been replaced wholesale kept its fingerprint, so `calibrate-cases --reuse-from` returned
the previous p_hat for code it had never run — a plausible number measured on something else.
"""

from aibench.calibrate import plan_calibration
from aibench.models import Case
from aibench.validity import FINGERPRINT_VERSION, case_fingerprint, set_fingerprint


def _raw(**over) -> dict:
    base = {
        "case_id": "fp-test",
        "schema_version": "0.1",
        "task_type": "bugfix",
        "language": "python",
        "prompt": "Make the suite pass.",
        "context": {
            "files": [
                {"path": "impl.py", "content": "def thing():\n    return 0\n"},
                {
                    "path": "test_impl.py",
                    "content": "from impl import thing\n\ndef test_t():\n    assert thing() == 1\n",
                },
            ]
        },
        "grader": {
            "mode": "script",
            "command": "python -m pytest -q",
            "gold_files": [{"path": "impl.py", "content": "def thing():\n    return 1\n"}],
        },
        "metadata": {},
    }
    base.update(over)
    return base


def _case(**over) -> Case:
    return Case.from_dict(_raw(**over))


class TestContentIsPartOfIdentity:
    def test_editing_a_context_file_changes_the_fingerprint(self):
        before = case_fingerprint(_case())
        ctx = {
            "files": [
                {"path": "impl.py", "content": "def thing():\n    return 99  # rewritten\n"},
                {
                    "path": "test_impl.py",
                    "content": "from impl import thing\n\ndef test_t():\n    assert thing() == 1\n",
                },
            ]
        }
        assert case_fingerprint(_case(context=ctx)) != before

    def test_replacing_the_reference_solution_changes_the_fingerprint(self):
        before = case_fingerprint(_case())
        grader = {
            "mode": "script",
            "command": "python -m pytest -q",
            "gold_files": [{"path": "impl.py", "content": "def thing():\n    return 2\n"}],
        }
        assert case_fingerprint(_case(grader=grader)) != before

    def test_changing_a_hidden_test_changes_the_fingerprint(self):
        grader = {
            "mode": "script",
            "command": "python -m pytest -q",
            "gold_files": [{"path": "impl.py", "content": "def thing():\n    return 1\n"}],
            "hidden_tests": [{"path": "test_h.py", "content": "def test_h():\n    assert True\n"}],
        }
        before = case_fingerprint(_case(grader=grader))
        grader2 = dict(grader)
        grader2["hidden_tests"] = [
            {"path": "test_h.py", "content": "def test_h():\n    assert False\n"}
        ]
        assert case_fingerprint(_case(grader=grader2)) != before

    def test_an_unchanged_case_keeps_its_fingerprint(self):
        assert case_fingerprint(_case()) == case_fingerprint(_case())

    def test_the_dict_and_case_forms_agree(self):
        # Both forms are accepted; disagreeing would make identity depend on the caller.
        assert case_fingerprint(_case()) == case_fingerprint(_raw())

    def test_reordering_context_files_changes_the_fingerprint(self):
        # The agent prompt lists files in declaration order, so a reordering is a different
        # prompt even though the set of files is identical.
        base = _raw()
        before = case_fingerprint(Case.from_dict(base))
        ctx = {"files": list(reversed(base["context"]["files"]))}
        assert case_fingerprint(_case(context=ctx)) != before

    def test_the_set_fingerprint_moves_with_its_cases(self):
        before = set_fingerprint([_case()])
        ctx = {"files": [{"path": "impl.py", "content": "changed\n"}]}
        assert set_fingerprint([_case(context=ctx)]) != before


class TestTheGraderIsPartOfIdentity:
    """Changing how a submission is judged changes the case, even when no file moves.

    Swapping `grader.command` for one that always fails, with everything else untouched, left
    the fingerprint unchanged — so `--reuse-from` reported the old p_hat for a case that now
    scores zero. Repairing a grader command or adding an install step is exactly the edit this
    work motivates, and it was invisible to the gate.
    """

    def _differs(self, **grader_over) -> bool:
        base = _raw()["grader"]
        return case_fingerprint(_case(grader={**base, **grader_over})) != case_fingerprint(_case())

    def test_a_changed_command_changes_the_fingerprint(self):
        assert self._differs(command="python -m pytest -q other_test.py")

    def test_a_changed_mode_changes_the_fingerprint(self):
        assert self._differs(mode="gold")

    def test_a_changed_match_changes_the_fingerprint(self):
        assert self._differs(match="contains_key_lines")

    def test_changed_key_lines_change_the_fingerprint(self):
        assert self._differs(key_lines=["def thing"])

    def test_changed_protected_paths_change_the_fingerprint(self):
        assert self._differs(protected_paths=["test_impl.py"])

    def test_a_changed_judge_threshold_changes_the_fingerprint(self):
        assert self._differs(judge_threshold=0.9)

    def test_a_changed_language_changes_the_fingerprint(self):
        assert case_fingerprint(_case(language="javascript")) != case_fingerprint(_case())


class TestExternalWorkspaces:
    """Code that lives outside the case JSON cannot be witnessed by hashing the case JSON."""

    def test_an_inline_case_is_not_external(self):
        from aibench.validity import external_workspace

        assert external_workspace(_case()) is False

    def test_a_snapshot_case_is_external(self):
        from aibench.validity import external_workspace

        ctx = {"files": [], "workspace": {"mode": "snapshot", "snapshot": {"path": "snap"}}}
        assert external_workspace(_case(context=ctx)) is True
        assert external_workspace(_raw(context=ctx)) is True

    def test_a_changed_workspace_spec_changes_the_fingerprint(self):
        a = {"files": [], "workspace": {"mode": "snapshot", "snapshot": {"path": "repo_a"}}}
        b = {"files": [], "workspace": {"mode": "snapshot", "snapshot": {"path": "repo_b"}}}
        assert case_fingerprint(_case(context=a)) != case_fingerprint(_case(context=b))

    def test_an_external_workspace_case_is_never_reused(self):
        # Its fingerprint cannot move when the snapshot does, so the only safe answer is to
        # re-run it rather than trust a value that cannot witness the change.
        fp = f"{FINGERPRINT_VERSION}:same"
        previous = {"anchor_fingerprint": "p1", "cases": [{"case_id": "c1", "fingerprint": fp}]}
        todo, reused = plan_calibration(
            ["c1"], {"c1": fp}, previous, panel="p1", never_reuse={"c1"}
        )
        assert todo == ["c1"] and reused == []

    def test_an_inline_case_with_the_same_shape_is_still_reused(self):
        fp = f"{FINGERPRINT_VERSION}:same"
        previous = {"anchor_fingerprint": "p1", "cases": [{"case_id": "c1", "fingerprint": fp}]}
        todo, reused = plan_calibration(["c1"], {"c1": fp}, previous, panel="p1", never_reuse=set())
        assert todo == [] and [c["case_id"] for c in reused] == ["c1"]


class TestVersionGate:
    def test_the_version_is_carried_in_the_value(self):
        assert case_fingerprint(_case()).startswith(f"{FINGERPRINT_VERSION}:")

    def test_results_from_an_older_scheme_are_never_reused(self):
        # A bare hex fingerprint is what the path-only scheme wrote. It cannot witness a
        # change of file contents, so reusing its p_hat would report a stale measurement.
        previous = {
            "anchor_fingerprint": "p1",
            "cases": [{"case_id": "c1", "fingerprint": "8e4d4f1a4266a179"}],
        }
        todo, reused = plan_calibration(
            ["c1"], {"c1": f"{FINGERPRINT_VERSION}:8e4d4f1a4266a179"}, previous, panel="p1"
        )
        assert todo == ["c1"]
        assert reused == []


class TestAnchorCoverage:
    """A calibration that lost passes must say so in its own numbers.

    `runs/calibration_20260805_150140/` is the real precedent: the process was killed after
    four of six passes, and the surviving rows carry nothing that says the panel was smaller
    than configured. Comparing them against a full-panel result produced a plausible +10.9pp
    that was entirely the missing anchor.
    """

    def test_an_anchor_that_produced_no_rows_is_reported_missing(self):
        from aibench.calibrate import anchor_coverage

        runs = [
            {"anchor": "weak", "rows": [{"case_id": "c1"}]},
            {"anchor": "mid", "rows": [{"case_id": "c1"}]},
            {"anchor": "strong", "rows": []},
        ]
        cov = anchor_coverage(["weak", "mid", "strong"], runs)
        assert cov["anchors_missing"] == ["strong"]
        assert cov["anchors_with_rows_this_run"] == {"mid": 1, "strong": 0, "weak": 1}

    def test_an_anchor_that_never_ran_at_all_is_reported_missing(self):
        from aibench.calibrate import anchor_coverage

        cov = anchor_coverage(["weak", "mid", "strong"], [{"anchor": "weak", "rows": [{}]}])
        assert cov["anchors_missing"] == ["mid", "strong"]

    def test_a_complete_panel_reports_nothing_missing(self):
        from aibench.calibrate import anchor_coverage

        runs = [{"anchor": n, "rows": [{}]} for n in ("weak", "mid", "strong")]
        assert anchor_coverage(["weak", "mid", "strong"], runs)["anchors_missing"] == []

    def test_a_fully_reused_calibration_is_not_reported_as_having_no_anchors(self):
        # Reused cases were measured by anchors this invocation never ran. Judging coverage on
        # the run list alone would flag a complete panel as missing every anchor, and a field
        # that cries wolf on a complete calibration trains the next reader to ignore it.
        from aibench.calibrate import anchor_coverage

        cases = [{"case_id": "c1", "by_anchor": {"weak": 1.0, "mid": 0.5, "strong": 0.0}}]
        cov = anchor_coverage(["weak", "mid", "strong"], [], cases=cases)
        assert cov["anchors_missing"] == []
        assert cov["anchors_of_record"] == ["mid", "strong", "weak"]
        assert cov["anchors_with_rows_this_run"] == {}

    def test_repeats_of_one_anchor_are_summed_not_overwritten(self):
        from aibench.calibrate import anchor_coverage

        runs = [{"anchor": "weak", "rows": [{}, {}]}, {"anchor": "weak", "rows": [{}]}]
        assert anchor_coverage(["weak"], runs)["anchors_with_rows_this_run"] == {"weak": 3}


class TestNoStoredFingerprintIsTrusted:
    def test_a_stale_annotation_does_not_decide_identity(self):
        # `metadata.fingerprint` is whatever the last `audit-cases --annotate` left behind.
        # Trusting it makes reuse depend on when annotation ran, not on the case contents.
        case = _case(metadata={"fingerprint": f"{FINGERPRINT_VERSION}:deadbeefdeadbeef"})
        assert case_fingerprint(case) != case.metadata["fingerprint"]


class TestReusedRowsAreRejudged:
    """`--reuse-from` reuses the measurement, never the verdict.

    Tuning p_max/p_min/min_rpb needs no re-measurement, so that is exactly when an operator
    reaches for --reuse-from — and the reused rows used to carry the previous run's keep/drop
    decisions under a report stating the new thresholds.
    """

    def _row(self, **over):
        row = {
            "case_id": "c1",
            "p_hat": 0.5,
            "point_biserial": 0.9,
            "keep": True,
            "reasons": [],
            "tier": None,
        }
        row.update(over)
        return row

    def test_a_tightened_threshold_re_judges_a_reused_row(self):
        from aibench.calibrate import SelectionPolicy, _merge_reused

        merged = _merge_reused(
            {"cases": []}, [self._row()], policy=SelectionPolicy(p_max=0.4), tiers=None
        )
        row = merged["cases"][0]
        assert row["keep"] is False
        assert row["reasons"] == ["too_easy(p=0.50>0.4)"]
        assert merged["kept_count"] == 0

    def test_a_loosened_threshold_re_admits_a_reused_row(self):
        from aibench.calibrate import SelectionPolicy, _merge_reused

        dropped = self._row(keep=False, reasons=["too_easy(p=0.50>0.4)"])
        merged = _merge_reused(
            {"cases": []}, [dropped], policy=SelectionPolicy(p_max=0.9), tiers=None
        )
        assert merged["cases"][0]["keep"] is True
        assert merged["cases"][0]["reasons"] == []

    def test_a_retagged_case_does_not_keep_its_old_tier(self):
        # tier is deliberately outside the fingerprint — it cannot change p_hat — so a retag
        # would otherwise survive into the quota that decides what ships.
        from aibench.calibrate import SelectionPolicy, _merge_reused

        merged = _merge_reused(
            {"cases": []}, [self._row(tier=None)], policy=SelectionPolicy(), tiers={"c1": "T3"}
        )
        assert merged["cases"][0]["tier"] == "T3"
        assert merged["tier_distribution"] == {"T3": 1}

    def test_verdict_reasons_is_a_pure_function_of_the_measurement(self):
        from aibench.calibrate import SelectionPolicy, verdict_reasons

        pol = SelectionPolicy(p_max=0.9, p_min=0.05, min_rpb=0.15)
        assert verdict_reasons(0.5, 0.9, pol) == []
        assert verdict_reasons(0.95, 0.9, pol) == ["too_easy(p=0.95>0.9)"]
        assert verdict_reasons(0.0, 0.9, pol)[0].startswith("unsolved_by_all")
        assert verdict_reasons(0.5, 0.0, pol) == ["no_discrimination(r_pb=0.00<0.15)"]
