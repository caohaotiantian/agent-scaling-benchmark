"""A case set is data the harness received, not code the harness wrote.

`export-bundle` exists to hand a case set to another person, and `_clean2026` ships with the
repository, so the JSON reaching `run` and `audit-cases` comes from outside. These tests hold
the boundary that follows from that: nothing in a case JSON reaches a shell, and the code being
graded does not get the caller's credentials.
"""

from __future__ import annotations

import inspect
import io
import json
import os
import re

import pytest

from aibench.grading import (
    _GRADER_ENV_ALLOWLIST,
    _grade_gold,
    _grader_env,
    detect_grading_interference,
    is_collection_control,
)
from aibench.io_util import repo_root, safe_case_id, safe_command
from aibench.models import Case, RunConfig
from aibench.validity import check_gold_is_not_collection_control
from aibench.workspace import (
    WorkspaceSpec,
    _apply_snapshot,
    _resolve_snapshot,
    _safe_extract_tar,
    _safe_extract_zip,
    confined_path,
)

_MINIMAL_RUN = {
    "experiment_name": "e",
    "algorithm_name": "a",
    "algorithm_version": "v",
    "budget_axis": "calls",
    "budget_value": "1",
    "branches": 1,
    "max_attempts": 1,
    "max_steps": 1,
    "max_wall_time_s": 1,
    "selection_strategy": "first-submit",
    "case_set": "seed-v0",
    "benchmark_name": "b",
    "grouping": "task_type",
    "agent_config": "a.yaml",
    "model_config": "m.yaml",
}


def _seed() -> dict:
    path = repo_root() / "tests/fixtures/case_sets/seed-v0/case_001_fizzbuzz.json"
    return json.loads(path.read_text(encoding="utf-8"))


class TestACaseCommandNeverReachesAShell:
    """`grader.command` ran through `sh -c` with the caller's whole environment, so a case set
    was arbitrary code on the host of anyone who ran `audit-cases` on it."""

    @pytest.mark.parametrize(
        "command",
        [
            "python -m pytest -q; touch /tmp/x",
            "python -m pytest -q > /tmp/x",
            "python -m pytest -q && curl http://example.invalid",
            "python -m pytest -q $(id)",
            "python -m pytest -q `id`",
            "python -m pytest -q | tee /tmp/x",
            "python -m pytest -q & sleep 5",
            "python -m pytest -q\ntouch /tmp/x",
            "python -m pytest -q ~/secret",
            "python -m pytest -q *.py",
        ],
    )
    def test_shell_syntax_is_refused(self, command):
        with pytest.raises(ValueError, match="not run through a shell"):
            safe_command(command, field="grader.command")

    @pytest.mark.parametrize(
        ("command", "argv"),
        [
            ("python -m pytest -q", ["python", "-m", "pytest", "-q"]),
            ("node --test", ["node", "--test"]),
            ("python -m pytest -q test_calc.py", ["python", "-m", "pytest", "-q", "test_calc.py"]),
            ('echo "a b"', ["echo", "a b"]),
        ],
    )
    def test_the_commands_that_ship_still_parse(self, command, argv):
        assert safe_command(command, field="grader.command") == argv

    def test_an_unparseable_command_is_refused(self):
        with pytest.raises(ValueError, match="cannot be parsed"):
            safe_command('python -m pytest -q "unbalanced', field="grader.command")

    def test_loading_a_case_refuses_it(self):
        raw = _seed()
        raw["grader"]["command"] = "python -m pytest -q; touch /tmp/x"
        with pytest.raises(ValueError, match=re.escape("grader.command")):
            Case.from_dict(raw)

    def test_setup_commands_are_held_to_the_same_rule(self):
        """They run during materialization, so `audit-cases` executed them before grading."""
        with pytest.raises(ValueError, match=re.escape("workspace.setup_commands")):
            WorkspaceSpec.from_dict({"mode": "inline", "setup_commands": ["echo hi > /tmp/x"]})
        spec = WorkspaceSpec.from_dict({"mode": "inline", "setup_commands": ["echo hi"]})
        assert spec.setup_commands == ["echo hi"]


class TestValidateCasesSeesWhatTheRunningCommandsRefuse:
    """`validate-cases` executes nothing, which is what makes it the safe first command on a set
    someone gave you — so it has to refuse everything the executing commands would."""

    def test_it_reports_a_shell_command(self, tmp_path, monkeypatch):
        from aibench.cases import validate_case_set

        case_root = tmp_path / "cases"
        (case_root / "probe").mkdir(parents=True)
        raw = _seed()
        raw["grader"]["command"] = "python -m pytest -q; touch /tmp/x"
        (case_root / "probe" / "c.json").write_text(json.dumps(raw), encoding="utf-8")
        monkeypatch.setenv("AIBENCH_CASE_ROOT", str(case_root))
        errors = validate_case_set("probe")
        assert any("not run through a shell" in e for e in errors)

    def test_the_sets_that_ship_still_validate(self):
        from aibench.cases import validate_case_set

        assert validate_case_set("seed-v0") == []


class TestTheGradedCodeCannotReadTheCallersCredentials:
    """The grader is handed code the model wrote or the case set shipped, and the caller's
    environment holds `OPENAI_API_KEY` and `AIBENCH_DB_URL`."""

    def test_credentials_are_not_inherited(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-not-a-real-key")
        monkeypatch.setenv("AIBENCH_DB_URL", "mysql+pymysql://u:p@h/db")
        monkeypatch.setenv("GH_PAT", "not-a-real-token")
        env = _grader_env()
        assert "OPENAI_API_KEY" not in env
        assert "AIBENCH_DB_URL" not in env
        # A denylist of credential-shaped names would have kept this one.
        assert "GH_PAT" not in env

    def test_what_a_suite_needs_survives(self, monkeypatch):
        monkeypatch.setenv("PATH", "/usr/bin")
        env = _grader_env()
        assert env["PATH"] == "/usr/bin"
        assert env["PYTHONHASHSEED"] == "0"
        assert env["PYTHONDONTWRITEBYTECODE"] == "1"
        assert set(env) <= set(_GRADER_ENV_ALLOWLIST) | {
            "PYTHONHASHSEED",
            "PYTHONDONTWRITEBYTECODE",
        }

    def test_the_run_config_can_name_an_exception(self, monkeypatch):
        monkeypatch.setenv("HF_TOKEN", "not-a-real-token")
        assert "HF_TOKEN" not in _grader_env()
        assert _grader_env(("HF_TOKEN",))["HF_TOKEN"] == "not-a-real-token"

    def test_the_exception_list_comes_from_the_run_config(self):
        cfg = RunConfig.from_dict({**_MINIMAL_RUN, "grader_env_passthrough": ["HF_TOKEN"]})
        assert cfg.grader_env_passthrough == ("HF_TOKEN",)
        assert RunConfig.from_dict({**_MINIMAL_RUN}).grader_env_passthrough == ()


class TestTheShellAdapterQuotesWhatTheCaseControls:
    """The `command_template` is the operator's and may use shell syntax — that is the adapter's
    purpose. `{case_id}` is substituted into it and comes from the case JSON."""

    def test_case_id_is_quoted(self, tmp_path):
        from aibench.agents.shell_agent import ShellAgent
        from aibench.models import AgentConfig, ModelConfig

        marker = tmp_path / "INJECTED.txt"
        agent = ShellAgent(
            AgentConfig.from_dict(
                {
                    "name": "sh",
                    "adapter": "shell",
                    "version": "1",
                    "options": {"command_template": "echo {case_id}"},
                }
            ),
            ModelConfig.from_dict({"name": "m", "model": "m", "provider": "openai_compat"}),
        )
        raw = _seed()
        # `safe_case_id` already refuses this shape; the quoting is the second layer.
        raw["case_id"] = "benign"
        case = Case.from_dict(raw)
        object.__setattr__(case, "case_id", f"benign; touch {marker}")
        ws = tmp_path / "ws"
        ws.mkdir()
        agent.run(case, ws, max_steps=1, max_wall_time_s=30)
        assert not marker.exists()


class TestSymlinksInTheWorkspaceAreNotAPathLayer:
    """`safe_relpath` is text and cannot see the filesystem. A workspace holding
    `vendor -> /elsewhere` keeps every component name relative while the join lands outside,
    which made the gold comparison a read oracle and hid files from the interference scan."""

    def _linked_workspace(self, tmp_path):
        ws, outside = tmp_path / "ws", tmp_path / "outside"
        ws.mkdir()
        outside.mkdir()
        (outside / "secret.txt").write_text("SUPER-SECRET-CANARY\n", encoding="utf-8")
        os.symlink(outside, ws / "vendor")
        return ws

    def test_confined_path_refuses_a_link_out(self, tmp_path):
        ws = self._linked_workspace(tmp_path)
        assert confined_path(ws, "ok.py") == (ws / "ok.py").resolve()
        with pytest.raises(ValueError, match="escapes workspace"):
            confined_path(ws, "vendor/secret.txt")

    def test_a_gold_comparison_cannot_read_through_one(self, tmp_path):
        ws = self._linked_workspace(tmp_path)
        raw = _seed()
        raw["grader"] = {
            "mode": "gold",
            "match": "exact",
            "gold_files": [{"path": "vendor/secret.txt", "content": "SUPER-SECRET-CANARY\n"}],
        }
        with pytest.raises(ValueError, match="escapes workspace"):
            _grade_gold(Case.from_dict(raw), ws)

    def test_a_symlinked_directory_is_interference(self, tmp_path):
        """`rglob` will not descend into it and pytest's `is_dir()` will, so everything behind
        it is imported by the grader and invisible to the scan."""
        ws = self._linked_workspace(tmp_path)
        raw = _seed()
        raw["grader"]["hidden_tests"] = [{"path": "impl_spec.py", "content": "#"}]
        verdict = detect_grading_interference(Case.from_dict(raw), ws)
        assert verdict is not None and "symlinked_dir" in verdict


class TestASnapshotsSymlinksAreDroppedAtEveryDepth:
    """Following one copies the target's contents in; recreating it leaves a live link that a
    later inline-file write walks out of. Neither: they are dropped, and reported."""

    def test_a_nested_symlink_is_neither_followed_nor_rebuilt(self, tmp_path):
        cs, outside = tmp_path / "cs", tmp_path / "outside"
        (cs / "snapshots" / "proj" / "sub").mkdir(parents=True)
        outside.mkdir()
        (outside / "secret.txt").write_text("CANARY\n", encoding="utf-8")
        os.symlink(outside, cs / "snapshots" / "proj" / "sub" / "esc")
        ws = tmp_path / "ws"
        ws.mkdir()
        dropped = _apply_snapshot("proj", ws, case_set_dir=cs)
        assert dropped == ["sub/esc"]
        assert not (ws / "sub" / "esc").exists()
        assert not any(p.is_symlink() for p in ws.rglob("*"))


class TestEveryNamePytestReadsIsGuarded:
    """`.pytest.ini` has the same effect as `pytest.ini` and was not on the list."""

    @pytest.mark.parametrize(
        "name",
        ["pytest.ini", ".pytest.ini", "pytest.toml", ".pytest.toml", "PYTEST.INI", "Conftest.py"],
    )
    def test_it_is_recognised(self, name):
        assert is_collection_control(name)

    def test_the_list_is_the_one_pytest_uses(self):
        from _pytest.config.findpaths import locate_config

        source = inspect.getsource(locate_config)
        names = re.findall(r'"([^"]+\.(?:ini|toml|cfg))"', source)
        assert names, "could not read pytest's config_names"
        missing = [n for n in names if not is_collection_control(n)]
        assert not missing, f"pytest reads these and the gate does not know them: {missing}"

    def test_the_audit_gate_folds_case_too(self):
        """`check_gold_is_not_collection_control` exists because the interference scan cannot
        arm for a case that ships one as its gold file."""
        raw = _seed()
        raw["grader"]["gold_files"] = [{"path": "PYTEST.INI", "content": "[pytest]\n"}]
        issues = check_gold_is_not_collection_control(Case.from_dict(raw))
        assert [i.code for i in issues] == ["gold_is_collection_control_file"]


class TestTheCaseIdGuardsAgreeWithEachOther:
    """Two layers only help if the inner one is not looser than the outer."""

    def test_a_trailing_newline_is_refused(self):
        # `$` matches before a trailing newline; `\\Z` does not.
        for cid in ("abc\n", "..\n", "ok\n"):
            with pytest.raises(ValueError, match="unsafe case_id"):
                safe_case_id(cid)

    def test_ids_differing_only_in_case_are_a_duplicate(self, tmp_path, monkeypatch):
        """They are one directory on APFS and NTFS, and materialization deletes it first."""
        from aibench.cases import validate_case_set

        root = tmp_path / "cases" / "probe"
        root.mkdir(parents=True)
        for name, cid in (("a.json", "Dup-Case"), ("b.json", "dup-case")):
            raw = _seed()
            raw["case_id"] = cid
            (root / name).write_text(json.dumps(raw), encoding="utf-8")
        monkeypatch.setenv("AIBENCH_CASE_ROOT", str(tmp_path / "cases"))
        assert any("duplicate case_id" in e for e in validate_case_set("probe"))


class TestTheGitSubdirIsConfinedToTheClone:
    """`repo_root / subdir` is a case-supplied join like the others, and was the one the first
    pass missed: it lands host files in the workspace, which the read tool and the llm_judge
    grader then send to the gateway."""

    def _local_repo(self, tmp_path):
        import subprocess

        repo = tmp_path / "src-repo"
        repo.mkdir()
        (repo / "app.py").write_text("x = 1\n", encoding="utf-8")
        for cmd in (
            ["git", "init", "-q", "-b", "main"],
            ["git", "-c", "user.email=t@e", "-c", "user.name=t", "add", "."],
            ["git", "-c", "user.email=t@e", "-c", "user.name=t", "commit", "-qm", "init"],
        ):
            subprocess.run(cmd, cwd=repo, check=True, capture_output=True)
        return repo

    def test_a_traversing_subdir_is_refused(self, tmp_path):
        from aibench.workspace import materialize_workspace

        repo = self._local_repo(tmp_path)
        secret = tmp_path / "HOSTDIR"
        secret.mkdir()
        (secret / "id_rsa").write_text("PRIVATE-KEY-CANARY\n", encoding="utf-8")
        raw = _seed()
        raw["context"]["files"] = []
        raw["context"]["workspace"] = {
            "mode": "git",
            "git": {"url": f"file://{repo}", "ref": "main", "subdir": "../../HOSTDIR"},
        }
        ws = tmp_path / "ws"
        # `strict` defaults to true for git mode, so the refusal is loud rather than a warning.
        with pytest.raises(RuntimeError, match="escapes workspace"):
            materialize_workspace(Case.from_dict(raw), ws, allow_network=True)
        assert not (ws / "id_rsa").exists()

    def test_a_real_subdir_still_works(self, tmp_path):
        from aibench.workspace import materialize_workspace

        repo = self._local_repo(tmp_path)
        (repo / "pkg").mkdir()
        (repo / "pkg" / "mod.py").write_text("y = 2\n", encoding="utf-8")
        import subprocess

        subprocess.run(
            ["git", "-c", "user.email=t@e", "-c", "user.name=t", "add", "."],
            cwd=repo,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "-c", "user.email=t@e", "-c", "user.name=t", "commit", "-qm", "pkg"],
            cwd=repo,
            check=True,
            capture_output=True,
        )
        raw = _seed()
        raw["context"]["files"] = []
        raw["context"]["workspace"] = {
            "mode": "git",
            "git": {"url": f"file://{repo}", "ref": "main", "subdir": "pkg"},
        }
        ws = tmp_path / "ws2"
        materialize_workspace(Case.from_dict(raw), ws, allow_network=True)
        assert (ws / "mod.py").is_file()


class TestTheGuardsThatHadNoTest:
    """Mutation-tested: each of these was neutered in turn and the whole suite stayed green,
    which means the next refactor could have removed them silently."""

    def test_a_snapshot_reached_through_a_symlink_is_refused(self, tmp_path):
        """The containment branch, as opposed to `safe_relpath` refusing the text first: only a
        symlink *inside* the case set can produce a name that is relative and lands outside."""
        cs, outside = tmp_path / "cs", tmp_path / "outside"
        (cs / "snapshots").mkdir(parents=True)
        (outside / "proj").mkdir(parents=True)
        (outside / "proj" / "a.py").write_text("x = 1\n", encoding="utf-8")
        os.symlink(outside / "proj", cs / "snapshots" / "proj")
        with pytest.raises(ValueError, match="escapes its case set"):
            _resolve_snapshot("proj", cs)

    def test_a_tar_special_file_is_refused(self, tmp_path):
        """`filter="data"` is what rejects device nodes and FIFOs; without it a member named
        `test_x.py` could be a FIFO that hangs the grader."""
        import tarfile

        dest = tmp_path / "ws"
        dest.mkdir()
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w") as tf:
            info = tarfile.TarInfo("test_hang.py")
            info.type = tarfile.FIFOTYPE
            tf.addfile(info)
        buf.seek(0)
        with (
            tarfile.open(fileobj=buf) as tf,
            pytest.raises(tarfile.SpecialFileError, match="is a special file"),
        ):
            _safe_extract_tar(tf, dest)
        assert not (dest / "test_hang.py").exists()

    def test_a_zip_prefix_sibling_member_is_refused(self, tmp_path):
        import zipfile

        dest = tmp_path / "ws"
        dest.mkdir()
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("../ws-evil/pwned.txt", "X")
        buf.seek(0)
        with (
            zipfile.ZipFile(buf) as zf,
            pytest.raises(RuntimeError, match="escapes workspace"),
        ):
            _safe_extract_zip(zf, dest)
        assert not (tmp_path / "ws-evil").exists()

    def test_the_case_dir_containment_is_a_second_layer(self, tmp_path, monkeypatch):
        """`safe_case_id` already refuses `..`, so this only fires if the first layer is gone —
        which is the point of having two, and the reason it needs its own test."""
        import aibench.runner as runner

        monkeypatch.setattr(runner, "safe_case_id", lambda cid: cid)
        raw = _seed()
        raw["case_id"] = "ok"
        case = Case.from_dict(raw)
        object.__setattr__(case, "case_id", "../../escape")
        with pytest.raises(ValueError, match="escapes run dir"):
            runner._run_one_case(
                case,
                run_dir=tmp_path / "run",
                cs="probe",
                agent_cfg=None,
                model_cfg=None,
                max_steps=1,
                max_wall_time_s=1,
            )

    @pytest.mark.parametrize("tool", ["read", "list"])
    def test_the_tool_loop_read_side_is_confined_too(self, tmp_path, tool):
        """Only the `write` handler had a test; `read` and `list` share the same check."""
        from aibench.agents.tool_loop import ToolLoopAgent
        from aibench.models import AgentConfig, ModelConfig

        ws = (tmp_path / "ws").resolve()
        ws.mkdir()
        sibling = tmp_path / "ws_sibling"
        sibling.mkdir()
        (sibling / "secret.txt").write_text("CANARY\n", encoding="utf-8")
        agent = ToolLoopAgent(
            AgentConfig.from_dict({"name": "tl", "adapter": "tool_loop", "version": "1"}),
            ModelConfig.from_dict({"name": "m", "model": "m", "provider": "openai_compat"}),
        )
        out = agent._run_tool(
            tool, {"path": "../ws_sibling/secret.txt"}, ws, allow_bash=False, written=[]
        )
        assert "path escape" in out
        assert "CANARY" not in out


class TestOneBadCaseDoesNotEndTheAudit:
    """`runner.py` learned this — "one case that made _grade_gold raise discarded results.jsonl,
    summary.json and report.md for the whole run" — and the audit gates did not."""

    def test_a_traversing_gold_path_is_a_verdict_not_a_crash(self, tmp_path):
        from aibench.validity import check_stub_fails

        raw = _seed()
        raw["grader"] = {
            "mode": "gold",
            "match": "exact",
            "gold_files": [{"path": "../outside.txt", "content": "x"}],
        }
        ok, detail = check_stub_fails(Case.from_dict(raw))
        assert ok is False
        assert "case_path_escapes_workspace" in detail
