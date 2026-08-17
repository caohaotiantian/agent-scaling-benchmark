from aibench.models import Case
from aibench.validity import audit_case, audit_case_set, case_fingerprint


def _case(**kwargs) -> Case:
    base = {
        "case_id": "v-test",
        "schema_version": "0.1",
        "task_type": "feature",
        "language": "python",
        "prompt": "Implement function add(a, b) returning sum of two integers in add.py",
        "context": {
            "files": [
                {"path": "add.py", "content": "def add(a, b):\n    raise NotImplementedError\n"},
                {
                    "path": "test_add.py",
                    "content": "from add import add\n\ndef test_add():\n    assert add(1, 2) == 3\n",
                },
            ]
        },
        "grader": {
            "mode": "script",
            "command": "python -m pytest -q test_add.py",
            "gold_files": [{"path": "add.py", "content": "def add(a, b):\n    return a + b\n"}],
        },
        "metadata": {},
    }
    base.update(kwargs)
    if "context" in kwargs:
        base["context"] = kwargs["context"]
    if "grader" in kwargs:
        base["grader"] = kwargs["grader"]
    return Case.from_dict(base)


def test_stub_fail_gate_passes_on_broken_stub():
    r = audit_case(_case())
    assert r.ok is True
    assert r.checks["stub_fail"]["ok"] is True
    assert r.difficulty in {"easy", "medium", "hard"}


def test_stub_fail_gate_detects_already_correct():
    c = _case(
        context={
            "files": [
                {"path": "add.py", "content": "def add(a, b):\n    return a + b\n"},
                {
                    "path": "test_add.py",
                    "content": "from add import add\n\ndef test_add():\n    assert add(1, 2) == 3\n",
                },
            ]
        }
    )
    r = audit_case(c)
    assert r.ok is False
    assert any(i.code == "stub_fail_gate" for i in r.issues)


def test_contamination_keyline():
    c = _case(
        grader={
            "mode": "gold",
            "match": "contains_key_lines",
            "key_lines": ["def add(a, b):", "return a + b"],
            "gold_files": [],
        },
        context={
            "files": [
                {
                    "path": "add.py",
                    "content": "def add(a, b):\n    return a + b\n",
                }
            ]
        },
    )
    r = audit_case(c)
    assert any("contamination" in i.code for i in r.issues)


def test_fingerprint_stable():
    c1 = _case()
    c2 = _case()
    assert case_fingerprint(c1) == case_fingerprint(c2)


def test_audit_seed_fixture_set():
    # seed-v0 lives in tests fixtures; should be loadable
    rep = audit_case_set("seed-v0")
    assert rep["total"] >= 3
    assert "content_fingerprint" in rep


def _t3_case_dict() -> dict:
    return {
        "case_id": "solvable-clamp",
        "schema_version": "0.1",
        "task_type": "bugfix",
        "language": "python",
        "prompt": "Callers report clamp() lets out-of-range values through. Make the tests pass.",
        "context": {
            "files": [
                {"path": "clamp.py", "content": "def clamp(x, lo, hi):\n    return x\n"},
                {
                    "path": "test_clamp.py",
                    "content": "from clamp import clamp\n\n\ndef test_inside():\n    assert clamp(5, 0, 9) == 5\n",
                    "role": "test",
                },
            ]
        },
        "grader": {
            "mode": "script",
            "command": "python -m pytest -q",
            "hidden_tests": [
                {
                    "path": "test_hidden.py",
                    "content": (
                        "from clamp import clamp\n\n\n"
                        "def test_below():\n    assert clamp(-1, 0, 9) == 0\n\n\n"
                        "def test_above():\n    assert clamp(99, 0, 9) == 9\n"
                    ),
                }
            ],
            "protected_paths": ["test_clamp.py"],
            "gold_files": [
                {
                    "path": "clamp.py",
                    "content": "def clamp(x, lo, hi):\n    return max(lo, min(hi, x))\n",
                }
            ],
        },
        "metadata": {"tier": "T3"},
    }


def test_solvability_gate_accepts_a_working_reference_solution():
    from aibench.validity import check_reference_solution

    ok, detail = check_reference_solution(Case.from_dict(_t3_case_dict()))
    assert ok is True, detail


def test_solvability_gate_catches_an_impossible_hidden_test():
    from aibench.validity import check_reference_solution

    raw = _t3_case_dict()
    raw["grader"]["hidden_tests"] = [
        {"path": "test_hidden.py", "content": "def test_impossible():\n    assert False\n"}
    ]
    ok, detail = check_reference_solution(Case.from_dict(raw))
    assert ok is False
    assert "reference_solution_failed" in detail


def test_audit_runs_both_bounds_and_tier_invariants():
    r = audit_case(Case.from_dict(_t3_case_dict()))
    assert r.tier == "T3"
    assert r.checks["stub_fail"]["ok"] is True
    assert r.checks["reference_solution"]["ok"] is True
    assert r.checks["tier"]["ok"] is True
    assert r.ok is True


def test_audit_reports_tier_violations_as_errors():
    raw = _t3_case_dict()
    raw["prompt"] = "clamp() uses the wrong comparison operator instead of min/max. Fix it."
    r = audit_case(Case.from_dict(raw))
    assert r.ok is False
    assert any(i.code == "tier_prompt_discloses_defect" for i in r.issues)


def test_a_script_case_without_a_reference_solution_is_refused():
    """Measured on a 59-case set: of the 18 cases no configuration could solve, 16 shipped
    without a reference solution, against 2 of 31 among cases that had one. Skipping the check
    for cases that cannot support it is precisely what let those 16 through — in the report
    they look like hard cases and are simply broken."""
    from aibench.validity import check_reference_solution

    raw = _t3_case_dict()
    raw["grader"].pop("gold_files")
    ok, detail = check_reference_solution(Case.from_dict(raw))
    assert ok is False
    assert "no_reference_solution" in detail

    report = audit_case(Case.from_dict(raw))
    assert report.ok is False
    assert any(i.code == "solvability_gate" for i in report.issues)


def test_a_key_lines_gold_case_has_no_reference_artifact_to_apply():
    """The key lines *are* the specification, so there is nothing to write into the workspace.
    Reporting that as `no_reference_solution` would restate a design choice as a defect."""
    from aibench.validity import check_reference_solution

    raw = _t3_case_dict()
    raw["grader"] = {"mode": "gold", "match": "contains_key_lines", "key_lines": ["def clamp"]}
    ok, detail = check_reference_solution(Case.from_dict(raw))
    assert ok is True
    assert detail.startswith("skipped_key_lines_only")


def test_an_llm_judge_case_is_exempt_because_the_gate_would_spend_model_calls():
    from aibench.validity import check_reference_solution, check_stub_fails

    raw = _t3_case_dict()
    raw["grader"] = {"mode": "llm_judge", "judge_rubric": "does it clamp"}
    assert check_reference_solution(Case.from_dict(raw)) == (True, "skipped_llm_judge")
    assert check_stub_fails(Case.from_dict(raw)) == (True, "skipped_llm_judge")


def test_a_gold_case_that_already_passes_untouched_is_rejected():
    """H3 + M4. `check_stub_fails` skipped every non-script case, so `audit_case` emitted
    `validity_ok: true` for gold cases whose envelope it had never measured — and with
    `contains_key_lines` the grader scans the whole workspace whenever no declared gold path is
    on disk, which is the normal shape for 1,939 of 1,941 gold cases."""
    from aibench.validity import check_stub_fails

    raw = _t3_case_dict()
    raw["grader"] = {
        "mode": "gold",
        "match": "contains_key_lines",
        # Already present in the shipped stub, so an untouched workspace satisfies the grader.
        "key_lines": ["def clamp"],
    }
    ok, detail = check_stub_fails(Case.from_dict(raw))
    assert ok is False
    assert detail == "stub_passed_grader"
