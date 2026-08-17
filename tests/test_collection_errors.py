"""A workspace that cannot be collected must not read as a case that is merely hard.

`returncode != 0` is the same signal for "the tests failed" and "the tests never ran", so a
case whose imports are missing satisfies the stub-fail gate by accident and then fails every
configuration equally — which is indistinguishable from difficulty in the reported numbers.

The classification tests below run the real runner rather than asserting against invented
output: every false positive and false negative worth catching here lives in the gap between
what a runner actually prints and what one imagines it prints.
"""

import shutil
import subprocess
import sys

import pytest

from aibench.languages import registered_spec, spec_for
from aibench.models import Case
from aibench.validity import audit_case, check_stub_fails


def _run(tmp_path, files: dict[str, str], cmd: str) -> tuple[int, str]:
    for name, body in files.items():
        (tmp_path / name).write_text(body, encoding="utf-8")
    proc = subprocess.run(
        cmd, shell=True, cwd=tmp_path, capture_output=True, text=True, timeout=120, check=False
    )
    return proc.returncode, f"{proc.stdout}\n{proc.stderr}"


PYTEST_CMD = f"{sys.executable} -m pytest -q"


class TestPythonAgainstRealPytest:
    def test_plain_failure_is_a_verdict_not_a_collection_error(self, tmp_path):
        code, out = _run(
            tmp_path,
            {"test_x.py": "def test_x():\n    assert 1 == 2\n"},
            PYTEST_CMD,
        )
        assert code == 1
        assert spec_for("python").is_uncollectable(code, out) is False

    def test_missing_import_at_module_level_is_a_collection_error(self, tmp_path):
        code, out = _run(
            tmp_path,
            {"test_x.py": "import nonexistent_dep_xyz\n\ndef test_x():\n    assert True\n"},
            PYTEST_CMD,
        )
        assert spec_for("python").is_uncollectable(code, out) is True

    def test_missing_import_inside_a_fixture_is_a_collection_error(self, tmp_path):
        # pytest reports this as `1 error` at exit 1 with no collection banner, so a matcher
        # keyed on the banner misses precisely the shape this phase exists to catch.
        code, out = _run(
            tmp_path,
            {
                "test_x.py": (
                    "import pytest\n\n"
                    "@pytest.fixture\n"
                    "def client():\n"
                    "    import nonexistent_dep_xyz\n"
                    "    return nonexistent_dep_xyz.Client()\n\n"
                    "def test_uses(client):\n"
                    "    assert client\n"
                )
            },
            PYTEST_CMD,
        )
        assert code == 1
        assert spec_for("python").is_uncollectable(code, out) is True

    def test_empty_suite_is_a_collection_error(self, tmp_path):
        code, out = _run(tmp_path, {"notatest.py": "x = 1\n"}, PYTEST_CMD)
        assert code == 5
        assert spec_for("python").is_uncollectable(code, out) is True

    def test_failure_whose_output_mentions_collection_wording_is_still_a_verdict(self, tmp_path):
        # The assertion diff echoes "no tests ran", which a text matcher would read as an
        # empty suite. The tally says otherwise: one test ran and failed.
        code, out = _run(
            tmp_path,
            {
                "summary.py": 'def summarise(n):\n    return f"{n} tests ran"\n',
                "test_summary.py": (
                    "from summary import summarise\n\n"
                    'def test_zero():\n    assert summarise(0) == "no tests ran"\n'
                ),
            },
            PYTEST_CMD,
        )
        assert code == 1
        assert spec_for("python").is_uncollectable(code, out) is False

    def test_teardown_error_alongside_real_verdicts_is_not_a_collection_error(self, tmp_path):
        # pytest counts a teardown error as `error` even though the tests ran and passed, so
        # `error > 0` cannot by itself mean "nothing ran".
        code, out = _run(
            tmp_path,
            {
                "test_x.py": (
                    "import pytest\n\n"
                    "@pytest.fixture\n"
                    "def broken():\n    yield 1\n    raise RuntimeError('teardown')\n\n"
                    "def test_a(broken):\n    assert broken == 1\n"
                )
            },
            PYTEST_CMD,
        )
        assert "error" in out
        assert spec_for("python").tally_counts(out)["passed"] >= 1
        assert spec_for("python").is_uncollectable(code, out) is False

    def test_failed_setup_step_is_a_collection_error(self, tmp_path):
        # A grader command with an install step that fails never reaches pytest, so nothing
        # passed and nothing failed.
        code, out = _run(
            tmp_path,
            {"test_x.py": "def test_x():\n    assert True\n"},
            f"{sys.executable} -m nonexistent_installer_xyz && {PYTEST_CMD}",
        )
        assert code != 0
        assert spec_for("python").is_uncollectable(code, out) is True


#: node's default reporter is TAP below v23 and `spec` from v23 on, and the two disagree about
#: exactly the text this module reads: TAP folds the dead subprocess's stderr into the stream as
#: comments (`# Node.js v20.19.5`), while `spec` leaves the banner bare and colours the totals.
#: Testing only the default means testing whichever node the machine happens to have — which is
#: how both shapes reached main broken at once, each invisible on the other's node. `None` keeps
#: the default covered, since that is the command cases actually ship with.
NODE_REPORTERS = [None, "spec", "tap"]


def _node_cmd(reporter: str | None) -> str:
    return "node --test" if reporter is None else f"node --test --test-reporter={reporter}"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
@pytest.mark.parametrize("reporter", NODE_REPORTERS)
class TestJavaScriptAgainstRealNode:
    def test_failing_test_is_a_verdict_not_a_collection_error(self, tmp_path, reporter):
        # node prints its totals for a failing test but crashes before them on a load error,
        # so the totals are the signal. A test asserting on a thrown SyntaxError — the classic
        # JSON.parse bugfix — must not be mistaken for a workspace that would not load.
        code, out = _run(
            tmp_path,
            {
                "config.mjs": "export function loadConfig(t) { return JSON.parse(t); }\n",
                "config.test.mjs": (
                    "import test from 'node:test';\n"
                    "import assert from 'node:assert';\n"
                    "import { loadConfig } from './config.mjs';\n"
                    "test('malformed input yields {}', () => {\n"
                    "  assert.deepStrictEqual(loadConfig('{oops'), {});\n"
                    "});\n"
                ),
            },
            _node_cmd(reporter),
        )
        assert code != 0
        assert "SyntaxError" in out
        assert spec_for("javascript").is_uncollectable(code, out) is False

    def test_missing_module_is_a_collection_error(self, tmp_path, reporter):
        # node reports the dead file as a failing test and still prints totals, so only its
        # crash banner distinguishes this from an ordinary failure.
        code, out = _run(
            tmp_path,
            {
                "x.test.mjs": (
                    "import test from 'node:test';\n"
                    "import { nope } from './does_not_exist.mjs';\n"
                    "test('t', () => { nope(); });\n"
                )
            },
            _node_cmd(reporter),
        )
        assert spec_for("javascript").tally_counts(out)["failed"] >= 1
        assert spec_for("javascript").is_uncollectable(code, out) is True

    def test_syntax_error_in_a_loaded_file_is_a_collection_error(self, tmp_path, reporter):
        code, out = _run(
            tmp_path,
            {
                "bad.mjs": "export function f( { return 1 }\n",
                "y.test.mjs": (
                    "import test from 'node:test';\n"
                    "import assert from 'node:assert';\n"
                    "import { f } from './bad.mjs';\n"
                    "test('t', () => { assert.equal(f(), 1); });\n"
                ),
            },
            _node_cmd(reporter),
        )
        assert spec_for("javascript").is_uncollectable(code, out) is True

    def test_a_failure_naming_a_module_error_code_is_still_a_verdict(self, tmp_path, reporter):
        # The assertion diff quotes ERR_MODULE_NOT_FOUND, but nothing failed to load.
        code, out = _run(
            tmp_path,
            {
                "z.test.mjs": (
                    "import test from 'node:test';\n"
                    "import assert from 'node:assert';\n"
                    "test('reports the code', () => {\n"
                    "  assert.equal('x', 'ERR_MODULE_NOT_FOUND');\n"
                    "});\n"
                )
            },
            _node_cmd(reporter),
        )
        assert "ERR_MODULE_NOT_FOUND" in out
        assert spec_for("javascript").is_uncollectable(code, out) is False


class TestBothNodeReporterShapesAreRead:
    """The regexes, pinned against captured output, for machines with no node at all.

    Everything above skips without a node binary, and each real-node test only ever sees the
    reporter its own node defaults to — so a shape can break with the suite fully green. These
    lines are copied verbatim from `node --test --test-reporter=<r>` on v24.16.0, both of whose
    formats are stable and version-selected rather than negotiated.
    """

    SPEC_TOTALS = "\x1b[34mℹ tests 1\x1b[39m\n\x1b[34mℹ pass 0\x1b[39m\n\x1b[34mℹ fail 1\x1b[39m\n"
    TAP_TOTALS = "# tests 1\n# pass 0\n# fail 1\n"

    def test_colour_codes_do_not_hide_the_totals(self):
        # node 23+ colours the summary whether or not stdout is a terminal, so the marker the
        # tally anchors on stops being the first character of the line.
        assert spec_for("javascript").tally_counts(self.SPEC_TOTALS) == {
            "passed": 0,
            "failed": 1,
            "error": 0,
        }

    def test_the_tap_totals_are_still_read(self):
        assert spec_for("javascript").tally_counts(self.TAP_TOTALS) == {
            "passed": 0,
            "failed": 1,
            "error": 0,
        }

    @pytest.mark.parametrize(
        ("banner", "reporter"),
        [("Node.js v24.16.0", "spec"), ("# Node.js v24.16.0", "tap")],
    )
    def test_the_crash_banner_is_seen_through_either_reporter(self, banner, reporter):
        # TAP comments out the dead subprocess's stderr; `spec` leaves it bare. Miss it and the
        # totals below say `fail 1`, which reads as a verdict — an unloadable workspace then
        # ships as a merely hard case.
        totals = self.TAP_TOTALS if reporter == "tap" else self.SPEC_TOTALS
        assert spec_for("javascript").is_uncollectable(1, f"{banner}\n{totals}") is True

    @pytest.mark.parametrize("totals", [SPEC_TOTALS, TAP_TOTALS])
    def test_a_plain_failure_stays_a_verdict_under_either_reporter(self, totals):
        assert spec_for("javascript").is_uncollectable(1, totals) is False


class TestOnlyThisRunnersOutputIsJudged:
    def test_a_bare_script_grader_is_not_judged_by_pytests_tally(self, tmp_path):
        # `python check.py` is an accepted grader command and prints no tally, so treating its
        # output as pytest's would call every genuine assertion failure a broken workspace.
        from aibench.grading import grade_case
        from aibench.workspace import materialize_workspace

        case = _case(
            files=[
                {"path": "impl.py", "content": "def thing():\n    return 0\n"},
                {
                    "path": "check.py",
                    "content": "from impl import thing\n\nassert thing() == 1\n",
                },
            ],
            gold=[{"path": "impl.py", "content": "def thing():\n    return 1\n"}],
            grader={"mode": "script", "command": "python check.py", "gold_files": []},
        )
        ws = tmp_path / "ws"
        materialize_workspace(case, ws, allow_network=False)
        grade = grade_case(case, ws)
        assert grade.passed is False
        assert grade.collection_error is False

    def test_the_pytest_runner_is_still_recognised(self):
        assert spec_for("python").drives("python -m pytest -q") is True
        assert spec_for("python").drives("python3 -m pytest -q test_x.py") is True
        assert spec_for("python").drives("python check.py") is False
        assert spec_for("javascript").drives("node --test") is True
        assert spec_for("javascript").drives("true") is False


class TestUnregisteredLanguages:
    def test_unregistered_language_is_never_judged_uncollectable(self):
        # spec_for falls back to Python; applying pytest's exit table to a Gradle or Go runner
        # would report an ordinary build verdict as a broken workspace.
        for lang in ("java", "cangjie", "go"):
            assert registered_spec(lang) is None


def _case(files: list[dict], gold: list[dict], **kw) -> Case:
    base = {
        "case_id": "collect-test",
        "schema_version": "0.1",
        "task_type": "bugfix",
        "language": "python",
        "prompt": "Make the suite pass.",
        "context": {"files": files},
        "grader": {"mode": "script", "command": "python -m pytest -q", "gold_files": gold},
        "metadata": {},
    }
    base.update(kw)
    return Case.from_dict(base)


_TEST_IMPL = "from impl import thing\n\ndef test_thing():\n    assert thing() == 1\n"


class TestStubGate:
    def test_workspace_broken_under_the_reference_solution_too_fails_the_gate(self):
        case = _case(
            files=[
                {"path": "impl.py", "content": "import nonexistent_dependency_xyz\n"},
                {"path": "test_impl.py", "content": _TEST_IMPL},
            ],
            gold=[{"path": "impl.py", "content": "import nonexistent_dependency_xyz\n"}],
        )
        report = audit_case(case)
        assert report.checks["stub_fail"]["ok"] is False
        assert report.checks["stub_fail"]["uncollectable"] is True

    def test_incomplete_stub_still_passes_when_the_reference_solution_collects(self):
        # "implement this": the visible test imports a symbol the stub has not defined yet, so
        # the stub cannot be collected — but the reference solution proves the workspace sound.
        case = _case(
            files=[
                {"path": "impl.py", "content": "# TODO: implement thing()\n"},
                {"path": "test_impl.py", "content": _TEST_IMPL},
            ],
            gold=[{"path": "impl.py", "content": "def thing():\n    return 1\n"}],
        )
        report = audit_case(case)
        assert report.checks["reference_solution"]["ok"] is True
        assert report.checks["stub_fail"]["ok"] is True
        assert report.checks["stub_fail"]["uncollectable"] is False

    def test_genuinely_failing_stub_still_passes_the_gate(self):
        case = _case(
            files=[
                {"path": "impl.py", "content": "def thing():\n    return 0\n"},
                {"path": "test_impl.py", "content": _TEST_IMPL},
            ],
            gold=[{"path": "impl.py", "content": "def thing():\n    return 1\n"}],
        )
        report = audit_case(case)
        assert report.checks["stub_fail"]["ok"] is True
        assert report.checks["stub_fail"]["uncollectable"] is False

    def test_an_unknown_reference_verdict_does_not_condemn_the_stub(self):
        # Without a reference verdict there is no evidence the workspace is broken. Claiming
        # one anyway would restate a case already rejected for having no reference solution.
        case = _case(
            files=[
                {"path": "impl.py", "content": "# TODO: implement thing()\n"},
                {"path": "test_impl.py", "content": _TEST_IMPL},
            ],
            gold=[],
        )
        ok, detail = check_stub_fails(case, reference_collects=None)
        assert ok is True
        assert detail == "stub_failed_as_expected"

    def test_a_complete_stub_that_cannot_be_collected_fails_even_when_the_gold_runs(self):
        """The reverse-construction hole: the two versions differ by an import, not the defect.

        Measured on 22 reverse-constructed cases, 5 of the 6 that passed this gate passed it
        this way — the pre-edit file imported numpy, pandas or torch and the post-edit file did
        not, so the tests separated the versions by which packages happened to be installed.
        Three then failed to collect on 8 of 9 calibration attempts and scored as hard cases.
        """
        case = _case(
            files=[
                {
                    "path": "impl.py",
                    "content": "import nonexistent_dependency_xyz\n\ndef thing():\n    return 0\n",
                },
                {"path": "test_impl.py", "content": _TEST_IMPL},
            ],
            gold=[{"path": "impl.py", "content": "def thing():\n    return 1\n"}],
            metadata={"generation": "reverse"},
        )
        report = audit_case(case)
        # The gold collects and passes, so the old rule called the workspace sound.
        assert report.checks["reference_solution"]["ok"] is True
        assert report.checks["stub_fail"]["ok"] is False
        assert report.checks["stub_fail"]["uncollectable"] is True

    def test_a_hollowed_out_stub_may_still_be_uncollectable(self):
        """Unchanged for forward generation, where the stub is the gold with a hole in it.

        There an uncollectable stub is the ordinary "implement this" shape: the visible test
        imports a symbol the stub has yet to define. Only a *complete* stub — one the trace
        shipped as a working file — makes that reading impossible.
        """
        case = _case(
            files=[
                {"path": "impl.py", "content": "# TODO: implement thing()\n"},
                {"path": "test_impl.py", "content": _TEST_IMPL},
            ],
            gold=[{"path": "impl.py", "content": "def thing():\n    return 1\n"}],
            metadata={"generation": "llm"},
        )
        report = audit_case(case)
        assert report.checks["stub_fail"]["ok"] is True
        assert report.checks["stub_fail"]["uncollectable"] is False

    def test_a_case_with_no_reference_solution_is_not_also_counted_as_uncollectable(self):
        case = _case(
            files=[
                {"path": "impl.py", "content": "import nonexistent_dependency_xyz\n"},
                {"path": "test_impl.py", "content": _TEST_IMPL},
            ],
            gold=[],
        )
        report = audit_case(case)
        assert report.checks["reference_solution"]["detail"].startswith("no_reference_solution")
        assert report.checks["stub_fail"]["uncollectable"] is False


class TestSolvabilityGate:
    def test_unparseable_reference_solution_is_reported_as_uncollectable(self):
        # The shape the secrets redaction produces: `access_token=***` leaves the string
        # literal unterminated, so the reference solution cannot be imported.
        case = _case(
            files=[
                {"path": "impl.py", "content": "def url():\n    return 'x'\n"},
                {
                    "path": "test_impl.py",
                    "content": "from impl import url\n\ndef test_url():\n    assert url() == 'ok'\n",
                },
            ],
            gold=[{"path": "impl.py", "content": "def url():\n    return 'ok?access_token=***\n"}],
        )
        ref = audit_case(case).checks["reference_solution"]
        assert ref["ok"] is False
        assert ref["uncollectable"] is True

    def test_working_reference_solution_still_passes(self):
        case = _case(
            files=[
                {"path": "impl.py", "content": "def thing():\n    return 0\n"},
                {"path": "test_impl.py", "content": _TEST_IMPL},
            ],
            gold=[{"path": "impl.py", "content": "def thing():\n    return 1\n"}],
        )
        ref = audit_case(case).checks["reference_solution"]
        assert ref["ok"] is True
        assert ref["uncollectable"] is False


class TestGreenRunsAreNeverBroken:
    """`passed` and `collection_error` must be mutually exclusive.

    A wholly skipped suite, an all-xfail suite and a `true` grader all exit 0 with no pass or
    fail counts, so judging them on the tally alone would call a green run broken. 15 drafts
    carry `"command": "true"` — none of them shipped, but the generator still emits the shape.
    """

    @pytest.mark.parametrize(
        ("name", "files", "command"),
        [
            (
                "all_skipped",
                {
                    "test_x.py": (
                        "import pytest\n\n"
                        '@pytest.mark.skip(reason="nope")\n'
                        "def test_a():\n    assert False\n"
                    )
                },
                PYTEST_CMD,
            ),
            (
                "all_xfail",
                {
                    "test_x.py": (
                        "import pytest\n\n@pytest.mark.xfail\ndef test_b():\n    assert False\n"
                    )
                },
                PYTEST_CMD,
            ),
            ("trivial_grader", {"impl.py": "x = 1\n"}, "true"),
        ],
    )
    def test_green_run_is_not_a_collection_error(self, tmp_path, name, files, command):
        from aibench.grading import grade_case
        from aibench.workspace import materialize_workspace

        case = _case(
            files=[{"path": p, "content": c} for p, c in files.items()],
            gold=[],
            grader={"mode": "script", "command": command},
        )
        ws = tmp_path / name
        materialize_workspace(case, ws, allow_network=False)
        grade = grade_case(case, ws)
        assert grade.passed is True
        assert grade.collection_error is False


class TestGradeResultPropagation:
    def test_composite_mode_does_not_gold_grade_an_uncollectable_workspace(self, tmp_path):
        from aibench.grading import grade_case
        from aibench.workspace import materialize_workspace

        case = _case(
            files=[
                {"path": "impl.py", "content": "import nonexistent_dependency_xyz\n"},
                {"path": "test_impl.py", "content": _TEST_IMPL},
            ],
            gold=[{"path": "impl.py", "content": "import nonexistent_dependency_xyz\n"}],
            grader={
                "mode": "composite",
                "command": "python -m pytest -q",
                "match": "normalized",
                "gold_files": [
                    {"path": "impl.py", "content": "import nonexistent_dependency_xyz\n"}
                ],
            },
        )
        ws = tmp_path / "ws"
        materialize_workspace(case, ws, allow_network=False)
        grade = grade_case(case, ws)
        assert grade.collection_error is True
        assert grade.passed is False

    def test_a_suite_that_never_ran_earns_no_partial_credit(self, tmp_path):
        from aibench.grading import grade_case
        from aibench.workspace import materialize_workspace

        case = _case(
            files=[
                {"path": "impl.py", "content": "import nonexistent_dependency_xyz\n"},
                {"path": "test_impl.py", "content": _TEST_IMPL},
            ],
            gold=[{"path": "impl.py", "content": "def thing():\n    return 1\n"}],
        )
        ws = tmp_path / "ws"
        materialize_workspace(case, ws, allow_network=False)
        grade = grade_case(case, ws)
        assert grade.collection_error is True
        assert grade.test_pass_ratio is None
