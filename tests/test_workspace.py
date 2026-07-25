from pathlib import Path

from aibench.models import Case
from aibench.workspace import WorkspaceSpec, materialize_workspace


def test_inline_materialize(tmp_path: Path):
    case = Case.from_dict(
        {
            "case_id": "t1",
            "schema_version": "0.1",
            "task_type": "feature",
            "language": "python",
            "prompt": "x",
            "context": {
                "files": [{"path": "a/b.py", "content": "print(1)\n"}],
            },
            "grader": {"mode": "gold", "key_lines": ["print"]},
        }
    )
    ws = tmp_path / "ws"
    mat = materialize_workspace(case, ws)
    assert (ws / "a/b.py").read_text(encoding="utf-8") == "print(1)\n"
    assert any(s.startswith("inline:") for s in mat.sources_applied)


def test_snapshot_with_inline_overlay(tmp_path: Path):
    snap = tmp_path / "snap"
    snap.mkdir()
    (snap / "calc.py").write_text("def add(a,b): return a+b\n", encoding="utf-8")
    case_set = tmp_path / "caseset"
    case_set.mkdir()
    # point snapshot path relative to case_set
    target = case_set / "snapshots" / "calc_project"
    target.parent.mkdir(parents=True)
    # copy via rename-like
    import shutil

    shutil.copytree(snap, target)

    case = Case.from_dict(
        {
            "case_id": "t2",
            "schema_version": "0.1",
            "task_type": "feature",
            "language": "python",
            "prompt": "x",
            "context": {
                "files": [
                    {"path": "calc.py", "content": "def add(a,b): return 0\n"},
                    {"path": "extra.txt", "content": "hi\n"},
                ],
                "workspace": {
                    "mode": "mixed",
                    "snapshot": {"path": "snapshots/calc_project"},
                },
            },
            "grader": {"mode": "script", "command": "true"},
        }
    )
    ws = tmp_path / "ws2"
    mat = materialize_workspace(case, ws, case_set_dir=case_set)
    # inline overlay wins
    assert (ws / "calc.py").read_text(encoding="utf-8") == "def add(a,b): return 0\n"
    assert (ws / "extra.txt").read_text(encoding="utf-8") == "hi\n"
    assert any("snapshot:" in s for s in mat.sources_applied)


def test_workspace_spec_from_dict():
    spec = WorkspaceSpec.from_dict(
        {
            "mode": "git",
            "git": {"url": "https://example.com/r.git", "ref": "abc", "subdir": "pkg"},
            "setup_commands": ["echo hi"],
            "env": {"FOO": "1"},
        }
    )
    assert spec.mode == "git"
    assert spec.git_url.endswith("r.git")
    assert spec.git_ref == "abc"
    assert spec.setup_commands == ["echo hi"]
