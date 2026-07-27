from aibench.cases import load_schema_validator
from aibench.extract.generate_case import heuristic_case_from_draft, is_safe_grader_command
from aibench.extract.sessions import filter_and_draft, load_sessions_from_export
from aibench.io_util import load_json, repo_root


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
