"""Case-supplied paths are generated text, so every filesystem join must be confined."""

from pathlib import Path

import pytest

from aibench.grading import grade_case
from aibench.models import Case
from aibench.validity import check_reference_solution
from aibench.workspace import materialize_workspace, safe_relpath


def test_safe_relpath_confines_absolute_and_traversing_paths():
    assert safe_relpath("/home/code/x.py") == "home/code/x.py"
    assert safe_relpath("pkg\\mod.py") == "pkg/mod.py"
    assert safe_relpath("/") == "unnamed"
    for escaping in ("../outside.py", "a/../../b.py"):
        with pytest.raises(ValueError, match="escapes workspace"):
            safe_relpath(escaping)


def _case(gold_path: str) -> Case:
    return Case.from_dict(
        {
            "case_id": "escape",
            "schema_version": "0.1",
            "task_type": "bugfix",
            "language": "python",
            "prompt": "Callers report clamp() ignores its bounds.",
            "context": {
                "files": [
                    {"path": "clamp.py", "content": "def clamp(x, lo, hi):\n    return x\n"},
                    {
                        "path": "test_clamp.py",
                        "content": "from clamp import clamp\n\n\n"
                        "def test_a():\n    assert clamp(9, 0, 5) == 5\n",
                        "role": "test",
                    },
                ]
            },
            "grader": {
                "mode": "script",
                "command": "python -m pytest -q",
                "gold_files": [
                    {
                        "path": gold_path,
                        "content": "def clamp(x, lo, hi):\n    return max(lo, min(hi, x))\n",
                    }
                ],
            },
            "metadata": {},
        }
    )


def test_an_absolute_reference_solution_path_stays_inside_the_workspace(tmp_path: Path):
    """A generated case really did ship `/home/code/...`, and the unsanitised join crashed the
    audit trying to mkdir it on the host. Sanitised, it lands in the throwaway workspace."""
    ok, detail = check_reference_solution(_case("/home/code/clamp.py"))
    assert not Path("/home/code").exists()
    # It writes inside the workspace, so it no longer overwrites the stub and the tests fail.
    assert ok is False
    assert "reference_solution_failed" in detail


def test_a_traversing_reference_solution_path_is_reported_not_written():
    ok, detail = check_reference_solution(_case("../../escaped.py"))
    assert ok is False
    assert "path_escapes_workspace" in detail
    assert not (Path.cwd().parent.parent / "escaped.py").exists()


def test_hidden_tests_and_inline_files_use_the_same_guard(tmp_path: Path):
    case = Case.from_dict(
        {
            "case_id": "hidden-escape",
            "schema_version": "0.1",
            "task_type": "bugfix",
            "language": "python",
            "prompt": "p",
            "context": {"files": [{"path": "/abs/mod.py", "content": "x = 1\n"}]},
            "grader": {
                "mode": "script",
                "command": "true",
                "hidden_tests": [
                    {"path": "/abs/test_hidden.py", "content": "def test_x():\n    pass\n"}
                ],
            },
            "metadata": {},
        }
    )
    ws = tmp_path / "ws"
    materialize_workspace(case, ws, allow_network=False)
    assert (ws / "abs" / "mod.py").is_file()

    grade_case(case, ws)
    assert (ws / "abs" / "test_hidden.py").is_file()
    assert not Path("/abs").exists()
