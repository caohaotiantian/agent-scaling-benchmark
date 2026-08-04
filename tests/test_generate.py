from aibench.cases import load_schema_validator
from aibench.extract.generate_case import (
    draft_tier,
    heuristic_case_from_draft,
    is_safe_grader_command,
)
from aibench.extract.sessions import filter_and_draft, load_sessions_from_export
from aibench.io_util import load_json, repo_root
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
