"""Case-supplied paths are generated text, so every filesystem join must be confined."""

import io
import json
import os
import tarfile
from pathlib import Path

import pytest

from aibench.grading import (
    _grade_gold,
    detect_grading_interference,
    grade_case,
    inject_hidden_tests,
)
from aibench.io_util import repo_root, safe_case_id
from aibench.models import Case
from aibench.validity import check_reference_solution
from aibench.workspace import (
    _apply_snapshot,
    _resolve_snapshot,
    _safe_extract_tar,
    materialize_workspace,
    safe_relpath,
)


def _seed_case() -> dict:
    """A case that is valid in every respect the test is not about."""
    path = repo_root() / "tests/fixtures/case_sets/seed-v0/case_001_fizzbuzz.json"
    return json.loads(path.read_text(encoding="utf-8"))


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


class TestTheCaseIdIsAlsoAPath:
    """`run_dir / "cases" / case_id` absorbs an absolute id and follows `..` out of the run.

    `materialize_workspace` then `shutil.rmtree`s the result, and `assert_disposable` refuses
    only paths that are or contain the checkout, so every other directory on the host was
    reachable for deletion.
    """

    @pytest.mark.parametrize(
        "case_id", ["../../../escape", "/etc/cron.d/evil", "..", ".", "a b", "x" * 129, ""]
    )
    def test_an_unsafe_id_is_refused(self, case_id):
        with pytest.raises(ValueError, match="unsafe case_id"):
            safe_case_id(case_id)

    @pytest.mark.parametrize("case_id", ["rev-98478339eba2368e", "seed-v0-001-fizzbuzz", "a.b_c-1"])
    def test_the_ids_that_ship_are_accepted(self, case_id):
        assert safe_case_id(case_id) == case_id

    def test_loading_a_case_refuses_it_too(self, tmp_path):
        """The schema `pattern` covers `validate-cases`; this covers every other entry point."""
        raw = _seed_case()
        raw["case_id"] = "../../evil"
        with pytest.raises(ValueError, match="unsafe case_id"):
            Case.from_dict(raw)


class TestGoldPathsCannotReadTheHost:
    """`_grade_gold` joined `workspace / gold.path` raw while the other six joins in the module
    went through `safe_relpath`, which made the pass/fail verdict an oracle for any readable
    file: `exact` compares it byte for byte, `contains_key_lines` reports substring presence."""

    @pytest.mark.parametrize("match", ["exact", "contains_key_lines"])
    def test_an_outside_gold_file_never_passes(self, tmp_path, match):
        secret = tmp_path / "outside" / "secret.txt"
        secret.parent.mkdir(parents=True)
        secret.write_text("CANARY\n", encoding="utf-8")
        ws = tmp_path / "ws"
        ws.mkdir()
        raw = _seed_case()
        raw["grader"] = {
            "mode": "gold",
            "match": match,
            "key_lines": ["CANARY"],
            "gold_files": [{"path": str(secret), "content": "CANARY\n"}],
        }
        case = Case.from_dict(raw)
        try:
            passed = _grade_gold(case, ws).passed
        except ValueError:
            passed = False
        assert not passed


class TestSnapshotsStayInsideTheirCaseSet:
    """`_resolve_snapshot` returned any absolute path that existed and fell back to `cwd`, so a
    case JSON could name a host directory. `_apply_snapshot` copies it into the workspace, from
    where the tool loop's read tool and the llm_judge grader send it to the gateway."""

    def _case_set(self, tmp_path):
        cs = tmp_path / "caseset"
        (cs / "snapshots" / "proj").mkdir(parents=True)
        (cs / "snapshots" / "proj" / "a.py").write_text("x = 1\n", encoding="utf-8")
        return cs

    def test_a_snapshot_the_case_set_ships_resolves(self, tmp_path):
        cs = self._case_set(tmp_path)
        assert _resolve_snapshot("proj", cs).is_dir()

    @pytest.mark.parametrize("path", ["../outside", "/etc", "snapshots/../../outside"])
    def test_a_snapshot_outside_the_case_set_is_refused(self, tmp_path, path):
        cs = self._case_set(tmp_path)
        (tmp_path / "outside").mkdir(exist_ok=True)
        with pytest.raises((ValueError, FileNotFoundError)):
            _resolve_snapshot(path, cs)

    def test_a_symlink_inside_a_snapshot_is_not_followed(self, tmp_path):
        cs = self._case_set(tmp_path)
        outside = tmp_path / "outside"
        outside.mkdir(exist_ok=True)
        (outside / "secret.txt").write_text("CANARY\n", encoding="utf-8")
        os.symlink(outside, cs / "snapshots" / "proj" / "link")
        ws = tmp_path / "ws"
        ws.mkdir()
        _apply_snapshot("proj", ws, case_set_dir=cs)
        assert (ws / "a.py").is_file()
        assert not (ws / "link" / "secret.txt").exists()


class TestHiddenTestsCannotBeRedirectedByAParentSymlink:
    """The leaf was checked for being a symlink and replaced — that is the anti-cheat path and
    it stays. A symlinked *parent* was not checked, and sends the write outside the workspace
    while every component name stays relative."""

    def test_a_symlinked_parent_is_refused(self, tmp_path):
        ws = tmp_path / "ws"
        ws.mkdir()
        outside = tmp_path / "outside"
        outside.mkdir()
        os.symlink(outside, ws / "pkg")
        raw = _seed_case()
        raw["grader"]["hidden_tests"] = [{"path": "pkg/t_spec.py", "content": "# hidden"}]
        with pytest.raises(ValueError, match="escapes workspace"):
            inject_hidden_tests(Case.from_dict(raw), ws)
        assert not (outside / "t_spec.py").exists()


class TestArchiveMembersAreComparedAsPaths:
    """`str(member).startswith(str(dest))` is true for `<dest>-evil`, which is a sibling, not a
    child. Python 3.13 also leaves `extraction_filter` unset, so `extractall` was fully trusted."""

    def test_a_prefix_sibling_member_is_refused(self, tmp_path):
        dest = tmp_path / "ws"
        dest.mkdir()
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w") as tf:
            info = tarfile.TarInfo("../ws-evil/pwned.txt")
            info.size = 1
            tf.addfile(info, io.BytesIO(b"X"))
        buf.seek(0)
        with (
            tarfile.open(fileobj=buf) as tf,
            pytest.raises(RuntimeError, match="escapes workspace"),
        ):
            _safe_extract_tar(tf, dest)
        assert not (tmp_path / "ws-evil").exists()


class TestCollectionControlFilesAreMatchedCaseInsensitively:
    """macOS and Windows hand pytest the file it asked for regardless of case, so `PYTEST.INI`
    silenced the hidden tests while the gate compared names exactly and saw nothing."""

    def test_an_upper_cased_pytest_ini_is_interference(self, tmp_path):
        ws = tmp_path / "ws"
        ws.mkdir()
        (ws / "PYTEST.INI").write_text(
            "[pytest]\naddopts = --ignore-glob=*_spec.py\n", encoding="utf-8"
        )
        raw = _seed_case()
        raw["grader"]["hidden_tests"] = [{"path": "impl_spec.py", "content": "# hidden"}]
        assert detect_grading_interference(Case.from_dict(raw), ws) is not None
