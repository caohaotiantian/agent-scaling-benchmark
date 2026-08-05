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

    def test_the_set_fingerprint_moves_with_its_cases(self):
        before = set_fingerprint([_case()])
        ctx = {"files": [{"path": "impl.py", "content": "changed\n"}]}
        assert set_fingerprint([_case(context=ctx)]) != before


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
        assert cov["anchors_with_rows"] == {"mid": 1, "strong": 0, "weak": 1}

    def test_an_anchor_that_never_ran_at_all_is_reported_missing(self):
        from aibench.calibrate import anchor_coverage

        cov = anchor_coverage(["weak", "mid", "strong"], [{"anchor": "weak", "rows": [{}]}])
        assert cov["anchors_missing"] == ["mid", "strong"]

    def test_a_complete_panel_reports_nothing_missing(self):
        from aibench.calibrate import anchor_coverage

        runs = [{"anchor": n, "rows": [{}]} for n in ("weak", "mid", "strong")]
        assert anchor_coverage(["weak", "mid", "strong"], runs)["anchors_missing"] == []

    def test_repeats_of_one_anchor_are_summed_not_overwritten(self):
        from aibench.calibrate import anchor_coverage

        runs = [{"anchor": "weak", "rows": [{}, {}]}, {"anchor": "weak", "rows": [{}]}]
        assert anchor_coverage(["weak"], runs)["anchors_with_rows"] == {"weak": 3}


class TestNoStoredFingerprintIsTrusted:
    def test_a_stale_annotation_does_not_decide_identity(self):
        # `metadata.fingerprint` is whatever the last `audit-cases --annotate` left behind.
        # Trusting it makes reuse depend on when annotation ran, not on the case contents.
        case = _case(metadata={"fingerprint": f"{FINGERPRINT_VERSION}:deadbeefdeadbeef"})
        assert case_fingerprint(case) != case.metadata["fingerprint"]
