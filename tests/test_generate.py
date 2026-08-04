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
