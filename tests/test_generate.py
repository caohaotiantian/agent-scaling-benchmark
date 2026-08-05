from aibench.cases import load_schema_validator
from aibench.extract.generate_case import (
    accept_rewrite,
    draft_tier,
    heuristic_case_from_draft,
    is_safe_grader_command,
)
from aibench.extract.sessions import filter_and_draft, load_sessions_from_export
from aibench.io_util import load_json, repo_root, write_json
from aibench.tiers import axes_for_tier


def test_safe_grader_command():
    assert is_safe_grader_command("python -m pytest -q")
    assert is_safe_grader_command("python -m pytest -q test_x.py")
    assert not is_safe_grader_command("rm -rf /")
    assert not is_safe_grader_command("pytest && curl evil")


def test_heuristic_from_fixture_sessions():
    raw = load_json(repo_root() / "tests/fixtures/sessions_min.json")
    sessions = load_sessions_from_export(raw["sessions"])
    drafts = filter_and_draft(sessions, max_cases=10)
    assert len(drafts) >= 1
    case = heuristic_case_from_draft(drafts[0])
    validator = load_schema_validator()
    errors = sorted(validator.iter_errors(case), key=lambda e: list(e.path))
    assert errors == [], errors
    assert case["metadata"]["generation"] == "heuristic"


def test_heuristic_case_carries_a_settled_tier():
    raw = load_json(repo_root() / "tests/fixtures/sessions_min.json")
    sessions = load_sessions_from_export(raw["sessions"])
    drafts = filter_and_draft(sessions, max_cases=10)
    case = heuristic_case_from_draft(drafts[0])
    meta = case["metadata"]
    assert meta["tier"] in {"T1", "T2", "T3", "T4", "T5"}
    assert meta["capability_axes"] == axes_for_tier(meta["tier"])
    assert meta["tier_requested"] == "T1"
    validator = load_schema_validator()
    assert sorted(validator.iter_errors(case), key=lambda e: list(e.path)) == []


def test_generation_honours_the_tier_the_trace_suggested():
    draft = {
        "case_id": "d1",
        "schema_version": "0.1",
        "task_type": "bugfix",
        "language": "python",
        "prompt": "x",
        "context": {"files": []},
        "grader": {"mode": "gold"},
        "metadata": {"tier": "T4"},
    }
    assert draft_tier(draft) == "T4"
    assert draft_tier({"metadata": {}}) == "T2"
    assert draft_tier({"metadata": {"tier": "nonsense"}}, default="T1") == "T1"


def test_generator_type_slips_are_coerced_not_rejected():
    """A batch of 69 lost 9 cases to `"schema_version": 0.1` arriving as a JSON number."""
    from aibench.extract.generate_case import _coerce_scalar_fields

    data = {"schema_version": 0.1, "language": None, "case_id": None, "task_type": "bug"}
    _coerce_scalar_fields(data)
    assert data["schema_version"] == "0.1"
    assert data["language"] == "python"
    assert data["case_id"] == ""
    assert data["task_type"] == "bugfix"

    validator = load_schema_validator()
    case = {
        "case_id": "x",
        "schema_version": 0.1,
        "task_type": "bugfix",
        "language": "python",
        "prompt": "p",
        "context": {"files": [{"path": "a.py", "content": "x=1\n"}]},
        "grader": {"mode": "script", "command": "python -m pytest -q"},
    }
    assert list(validator.iter_errors(case)), "a numeric schema_version must be invalid"
    _coerce_scalar_fields(case)
    assert sorted(validator.iter_errors(case), key=lambda e: list(e.path)) == []


def test_every_tier_brief_asks_for_a_reference_solution():
    """T1 and T2 did not, which is why cases at those tiers shipped unverifiable: 16 of the
    18 cases no configuration could solve had taken the missing-reference-solution exemption."""
    from aibench.extract.generate_case import _TIER_BRIEFS, _system_prompt_for_tier

    for tier in _TIER_BRIEFS:
        prompt = _system_prompt_for_tier(tier)
        assert "gold_files" in prompt, tier
        assert "solvability" in prompt.lower(), tier


def test_the_heuristic_fallback_cannot_smuggle_in_an_unverifiable_case(tmp_path, capsys):
    """The LLM path rejects a script case with no reference solution, then the fallback
    produced exactly that: all 21 unverifiable cases in a 126-case build came from it."""
    from aibench.cli import main

    drafts = tmp_path / "drafts"
    drafts.mkdir()
    write_json(
        drafts / "d.json",
        {
            "case_id": "d",
            "schema_version": "0.1",
            "task_type": "bugfix",
            "language": "python",
            "prompt": "Fix the helper so the suite passes.",
            "context": {"files": [{"path": "m.py", "content": "def f():\n    return 0\n"}]},
            "grader": {"mode": "script", "command": "python -m pytest -q"},
            "metadata": {},
        },
    )
    out = tmp_path / "cases"
    rc = main(
        [
            "generate-cases",
            "--input-dir",
            str(drafts),
            "--output-dir",
            str(out),
            "--heuristic-only",
            "--max-cases",
            "5",
        ]
    )
    assert rc == 1, "nothing verifiable should have been written"
    assert "no reference solution" in capsys.readouterr().out
    assert not list(out.glob("*.json"))


def test_a_narrated_rewrite_is_refused():
    """A case shipped with its prompt set to the model's own narration — "The user wants me to
    rewrite the coding task description..." — quoting the original inside, so it both failed to
    describe a task and still disclosed the defect."""
    from aibench.extract.generate_case import accept_rewrite

    original = "Users report placement across OSDs is skewed under the default weights."
    narrated = (
        "The user wants me to rewrite the coding task description to report only the "
        'observable symptom.\n\nOriginal:\n"Users report that data placement is skewed"'
    )
    assert accept_rewrite(original, narrated) == original

    for meta in ("I'll rewrite that.", "Here is the rewritten description:", "Sure, here you go"):
        assert accept_rewrite(original, meta) == original


def test_a_rewrite_that_still_discloses_is_refused():
    original = "The comparison is inverted, so it continues when it should stop."
    assert accept_rewrite(original, "The improvement comparison is inverted.") == original


def test_a_reasoning_dump_is_refused():
    original = "clamp() returns values outside the requested range."
    assert accept_rewrite(original, "x " * 500) == original


def test_a_genuine_symptom_only_rewrite_is_taken():
    from aibench.extract.generate_case import accept_rewrite

    original = "compute_strides uses the wrong loop order instead of accumulating from the right."
    good = "compute_strides returns [1, 3] for shape (2, 3); callers expect [3, 1]."
    assert accept_rewrite(original, good) == good
    assert accept_rewrite(original, f"```\n{good}\n```") == good


def test_an_empty_rewrite_keeps_the_original():
    assert accept_rewrite("original text", "") == "original text"
    assert accept_rewrite("original text", "   ") == "original text"
