"""The code paths that produced every published number, and had no test at all.

`docs/AUDIT-2026-08-17.md` §A.4 lists these as RP-33…RP-37, and Part B as H14 and H6. They
share a shape: the suite was green because nothing looked, not because the code was right.

Every test here fails at `982a9c4` except where a docstring says otherwise.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from aibench.io_util import load_json, repo_root
from aibench.models import AgentConfig, Case, ModelConfig

ROOT = repo_root()


def _case(**over) -> Case:
    base = {
        "case_id": "surface",
        "schema_version": "0.1",
        "task_type": "bugfix",
        "language": "python",
        "prompt": "clamp() lets values outside the range through.",
        "context": {
            "files": [
                {
                    "path": "clamp.py",
                    "content": "def clamp(x, lo, hi):\n    return x\n",
                    "role": "impl",
                },
                {
                    "path": "test_clamp.py",
                    "content": "from clamp import clamp\n\n\ndef test_i():\n    assert clamp(5,0,9)==5\n",
                    "role": "test",
                },
            ]
        },
        "grader": {"mode": "script", "command": "python -m pytest -q"},
        "metadata": {},
    }
    base.update(over)
    return Case.from_dict(base)


class TestTheAdaptersActuallyRun:
    """H14. `OpenAICompatAgent.run` and `BareModelAgent.run` are never executed by any test —
    `test_agent_config_precedence.py` asserts on `inspect.getsource` — and `ShellAgent` has
    none at all. These are the three code paths that turn a model into a number."""

    def _model(self) -> ModelConfig:
        from aibench.io_util import load_yaml

        return ModelConfig.from_dict(load_yaml(ROOT / "configs/models/glm52.yaml"))

    def _agent(self, relative: str):
        from aibench.agents.registry import create_agent
        from aibench.io_util import load_yaml

        return create_agent(AgentConfig.from_dict(load_yaml(ROOT / relative)), self._model())

    @pytest.mark.parametrize(
        "relative", ["configs/agents/openai_compat.yaml", "configs/agents/bare_model.yaml"]
    )
    def test_a_missing_credential_is_infra_not_a_failed_solve(
        self, relative, tmp_path, monkeypatch
    ):
        """The distinction the whole `effective_case_count` denominator rests on."""
        for var in ("OPENAI_API_KEY", "AIBENCH_API_KEY"):
            monkeypatch.delenv(var, raising=False)
        workspace = tmp_path / "ws"
        workspace.mkdir()
        result = self._agent(relative).run(_case(), workspace, max_steps=1, max_wall_time_s=5)
        assert result.status == "infra_error"
        assert "key" in (result.error_message or "").lower()

    def test_the_single_turn_adapter_writes_what_the_model_returned(self, tmp_path, monkeypatch):
        import aibench.retry as retry

        monkeypatch.setenv("OPENAI_API_KEY", "test-key")
        monkeypatch.setenv("OPENAI_BASE_URL", "http://127.0.0.1:1/v1")
        fixed = "def clamp(x, lo, hi):\n    return max(lo, min(hi, x))\n"
        # The adapter imports `retry_call` inside `run`, so the module attribute is the seam.
        monkeypatch.setattr(
            retry,
            "retry_call",
            lambda fn, **_: ({}, [{"path": "clamp.py", "content": fixed}], "done"),
        )
        workspace = tmp_path / "ws"
        workspace.mkdir()
        (workspace / "clamp.py").write_text(
            "def clamp(x, lo, hi):\n    return x\n", encoding="utf-8"
        )

        result = self._agent("configs/agents/openai_compat.yaml").run(
            _case(), workspace, max_steps=1, max_wall_time_s=30
        )
        assert result.status == "completed", result.error_message
        assert (workspace / "clamp.py").read_text(encoding="utf-8") == fixed

    def test_the_bare_model_adapter_writes_the_fenced_block(self, tmp_path, monkeypatch):
        import aibench.retry as retry

        monkeypatch.setenv("OPENAI_API_KEY", "test-key")
        monkeypatch.setenv("OPENAI_BASE_URL", "http://127.0.0.1:1/v1")
        fixed = "def clamp(x, lo, hi):\n    return max(lo, min(hi, x))\n"
        monkeypatch.setattr(retry, "retry_call", lambda fn, **_: ({}, fixed))
        workspace = tmp_path / "ws"
        workspace.mkdir()
        (workspace / "clamp.py").write_text(
            "def clamp(x, lo, hi):\n    return x\n", encoding="utf-8"
        )

        result = self._agent("configs/agents/bare_model.yaml").run(
            _case(), workspace, max_steps=1, max_wall_time_s=30
        )
        assert result.status == "completed", result.error_message
        assert (workspace / "clamp.py").read_text(encoding="utf-8") == fixed

    def test_the_shell_adapter_refuses_an_unconfigured_template(self, tmp_path):
        """`configs/agents/shell.yaml` ships with `command_template: ""`, so the shipped config
        is unusable — which was never asserted, and never said out loud."""
        agent = self._agent("configs/agents/shell.yaml")
        workspace = tmp_path / "ws"
        workspace.mkdir()
        result = agent.run(_case(), workspace, max_steps=1, max_wall_time_s=5)
        assert result.status == "infra_error"
        assert "command_template" in (result.error_message or "")

    def test_the_shell_adapter_runs_the_template_it_is_given(self, tmp_path):
        from aibench.agents.shell_agent import ShellAgent

        fixed = "def clamp(x, lo, hi):\n    return max(lo, min(hi, x))\n"
        script = tmp_path / "fake_cli.py"
        script.write_text(
            "import pathlib, sys\n"
            f"pathlib.Path(sys.argv[1], 'clamp.py').write_text({fixed!r}, encoding='utf-8')\n",
            encoding="utf-8",
        )
        cfg = AgentConfig.from_dict(
            {
                "name": "shell-test",
                "version": "1.0.0",
                "adapter": "shell",
                "options": {"command_template": f"{sys.executable} {script} {{workspace}}"},
            }
        )
        workspace = tmp_path / "ws"
        workspace.mkdir()
        result = ShellAgent(cfg, self._model()).run(
            _case(), workspace, max_steps=1, max_wall_time_s=30
        )
        assert result.status == "completed", result.error_message
        assert (workspace / "clamp.py").read_text(encoding="utf-8") == fixed


class TestTheSandboxIsAssertedPositively:
    """RP-36 and H6. The only sandbox assertion was `sandboxed is False`, which passes on a
    machine with no sandbox *and* on one where the wrapper silently stopped being applied. And
    two calibration runs on disk show an agent walking to `benchmarks/ai_coding/cases/` and
    reading its own case JSON — the answer key is empirically reachable, not theoretically."""

    def _agent(self):
        from aibench.agents.opencode import OpenCodeAgent
        from aibench.io_util import load_yaml

        return OpenCodeAgent(
            AgentConfig.from_dict(load_yaml(ROOT / "configs/agents/opencode.yaml")),
            ModelConfig.from_dict(load_yaml(ROOT / "configs/models/glm52.yaml")),
        )

    def test_the_wrapper_is_applied_when_a_sandbox_exists(self, tmp_path, monkeypatch):
        import aibench.agents.opencode as adapter

        fake = tmp_path / "sandbox-exec"
        fake.write_text('#!/bin/sh\nexec "$@"\n', encoding="utf-8")
        fake.chmod(0o755)
        monkeypatch.setattr(adapter, "_SANDBOX_EXEC", fake)

        command, sandboxed = self._agent()._wrap_in_sandbox(["opencode", "run"], tmp_path)
        assert sandboxed is True
        assert command[0] == str(fake)
        assert (tmp_path / "sandbox.sb").is_file()

    def test_the_profile_denies_the_directory_holding_the_answer_key(self, tmp_path):
        """The deny must name the repository. `str(ROOT) in profile` was satisfied by the
        *allow* line alone — point the deny at an unrelated directory and it still passed."""
        from aibench.agents.opencode import sandbox_profile

        secrets = tmp_path / "answer-key"
        profile = sandbox_profile(secrets, readable=(tmp_path / ".venv",))
        deny = [ln for ln in profile.splitlines() if ln.lstrip().startswith("(deny")]
        assert any(str(secrets) in ln for ln in deny), (
            f"no deny rule names the protected root; deny lines were: {deny}"
        )
        assert not any(str(secrets) in ln for ln in profile.splitlines() if "(allow" in ln)

    def test_no_sandbox_is_reported_as_no_sandbox(self, tmp_path, monkeypatch):
        import aibench.agents.opencode as adapter

        monkeypatch.setattr(adapter, "_SANDBOX_EXEC", tmp_path / "absent")
        command, sandboxed = self._agent()._wrap_in_sandbox(["opencode", "run"], tmp_path)
        assert sandboxed is False
        assert command == ["opencode", "run"]


class TestTheExtractionPathHasSignal:
    """RP-34. The DB-facing code that produced every case set had no test reaching it. A fake
    engine is enough: what matters is that the row shape the extractor expects is the row shape
    it gets, and that a trace with no usable pair is refused rather than half-built."""

    def _records(self) -> list:
        from aibench.extract.llm_chat_records import ChatRecord

        return [
            ChatRecord(
                request_id="req-1",
                start_time=None,
                model="GLM-5.2",
                requests_tags=json.dumps(["User-Agent: opencode/1.0"]),
                tools=json.dumps([{"function": {"name": "edit"}}]),
                full_history=json.dumps(self._history()),
                key_alias=None,
            )
        ]

    def _history(self) -> list[dict]:
        body = "def total(items):\n    return sum(items) - 1\n"
        history = [
            {"role": "user", "content": "totals are off by one"},
            {
                "role": "tool",
                "content": (
                    f"<path>calc.py</path>\n<type>file</type>\n<content>{body}\n"
                    "(End of file - total 2 lines)</content>"
                ),
            },
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "function": {
                            "name": "edit",
                            "arguments": json.dumps(
                                {
                                    "filePath": "calc.py",
                                    "oldString": "sum(items) - 1",
                                    "newString": "sum(items)",
                                }
                            ),
                        }
                    }
                ],
            },
        ]
        return history

    def test_a_trace_with_a_real_edit_becomes_a_draft_with_its_pair(self, monkeypatch):
        import aibench.extract.llm_chat_records as records

        monkeypatch.setattr(records, "fetch_chat_records", lambda *a, **k: self._records())
        drafts = records.extract_case_drafts_from_db(
            "mysql+pymysql://fake", limit=10, max_cases=5, require_edits=True
        )
        assert len(drafts) == 1
        versions = (drafts[0].get("metadata") or {}).get("file_versions") or []
        assert versions, "the edit must survive into the draft as a before/after pair"
        assert "sum(items) - 1" in versions[0]["pre"]
        assert "sum(items)\n" in versions[0]["post"]

    def test_a_trace_with_no_edit_is_refused_under_require_edits(self, monkeypatch):
        import aibench.extract.llm_chat_records as records

        records_in = self._records()
        # A user turn and a read, no edit: reverse construction has no pair to build from.
        records_in[0].full_history = json.dumps(self._history()[:2])
        monkeypatch.setattr(records, "fetch_chat_records", lambda *a, **k: records_in)
        drafts = records.extract_case_drafts_from_db(
            "mysql+pymysql://fake", limit=10, max_cases=5, require_usable_pair=True
        )
        assert drafts == []


class TestThePublishedCalibrationsAreChecked:
    """RP-35. No test asserted anything about the 13 tracked calibration artifacts or about any
    number the docs quote from them — so a file could drift from the aggregation that produced
    it and nothing would notice."""

    def _files(self) -> list[Path]:
        return sorted(
            p
            for p in (ROOT / "benchmarks/ai_coding/calibrations").glob("*.json")
            if p.name != "README.md"
        )

    def test_there_are_calibrations_to_check(self):
        assert len(self._files()) >= 10

    @pytest.mark.parametrize(
        "name",
        [
            p.name
            for p in sorted((repo_root() / "benchmarks/ai_coding/calibrations").glob("*.json"))
        ],
    )
    def test_each_file_is_internally_consistent(self, name):
        """Cheap, and it is the check that was missing: the summary counters must agree with
        the rows they summarise."""
        stored = load_json(ROOT / "benchmarks/ai_coding/calibrations" / name)
        cases = stored.get("cases")
        if isinstance(cases, int):
            # `bare-model-two-models_20260810.json` uses `cases` for a *count* and carries a
            # flat `rows` list instead — the shape RP-02 asks every export to adopt.
            assert stored["cases"] == len({r["case"] for r in stored["rows"]})
            return
        if cases is None:
            # The three ablation exports carry per-run aggregates, not per-case rows. That gap
            # is RP-06 and is not something a test can close.
            assert "runs" in stored or "pairwise_comparisons" in stored, name
            return
        assert stored["total_cases"] == len(cases)
        assert stored["kept_count"] == sum(1 for c in cases if c["keep"])
        assert stored["dropped_count"] == len(cases) - stored["kept_count"]
        for case in cases:
            if case["attempts"]:
                assert case["p_hat"] == pytest.approx(case["passes"] / case["attempts"]), (
                    f"{name}:{case['case_id']}"
                )
            assert case["keep"] == (not case["reasons"]), f"{name}:{case['case_id']}"


class TestAFixtureCaseSetCannotBeShadowed:
    """RP-37. `case_set_dir` preferred an untracked `benchmarks/…/cases/<name>` over the
    committed fixture, so a local `seed-v0` would silently replace the four cases every
    assertion in this suite is written against."""

    def test_the_fixture_prefix_never_leaves_the_fixtures(self):
        from aibench.cases import FIXTURE_PREFIX, case_set_dir

        resolved = case_set_dir(f"{FIXTURE_PREFIX}seed-v0")
        assert resolved == ROOT / "tests/fixtures/case_sets/seed-v0"

    def test_shadowing_a_fixture_name_is_refused(self, tmp_path, monkeypatch):
        """The accident the guard exists for: generating into the *default* corpus root, where
        a new `seed-v0` outranks the committed fixture and nothing says so."""
        import aibench.cases as cases_mod
        from aibench.cases import CASE_ROOT_ENV, case_set_dir

        monkeypatch.delenv(CASE_ROOT_ENV, raising=False)
        monkeypatch.setattr(cases_mod, "repo_root", lambda: tmp_path)
        (tmp_path / "benchmarks/ai_coding/cases/seed-v0").mkdir(parents=True)
        (tmp_path / "tests/fixtures/case_sets/seed-v0").mkdir(parents=True)
        with pytest.raises(ValueError, match="silently replace"):
            case_set_dir("seed-v0")

    def test_the_refusal_names_a_way_out(self, tmp_path, monkeypatch):
        """`rename it` was the only remedy offered, and it is not available to `promote` or
        `calibrate` — both of which resolve a name they were handed."""
        import aibench.cases as cases_mod
        from aibench.cases import CASE_ROOT_ENV, case_set_dir

        monkeypatch.delenv(CASE_ROOT_ENV, raising=False)
        monkeypatch.setattr(cases_mod, "repo_root", lambda: tmp_path)
        generated = tmp_path / "benchmarks/ai_coding/cases/seed-v0"
        fixture = tmp_path / "tests/fixtures/case_sets/seed-v0"
        generated.mkdir(parents=True)
        fixture.mkdir(parents=True)

        with pytest.raises(ValueError) as excinfo:
            case_set_dir("seed-v0")
        assert "generated:seed-v0" in str(excinfo.value)
        assert case_set_dir("generated:seed-v0") == generated
        assert case_set_dir("fixture:seed-v0") == fixture

    def test_an_isolated_root_is_not_treated_as_an_accident(self, tmp_path, monkeypatch):
        """Pointing `AIBENCH_CASE_ROOT` at a scratch directory is how an operator asks for an
        isolated corpus. Refusing `seed-v0` there — because the checkout ships a fixture of that
        name — made the override unusable for exactly the sets the suite cares about."""
        from aibench.cases import CASE_ROOT_ENV, case_set_dir

        monkeypatch.setenv(CASE_ROOT_ENV, str(tmp_path))
        (tmp_path / "seed-v0").mkdir()
        assert case_set_dir("seed-v0") == tmp_path / "seed-v0"

    def test_an_ordinary_name_still_prefers_the_generated_set(self, tmp_path, monkeypatch):
        from aibench.cases import CASE_ROOT_ENV, case_set_dir

        monkeypatch.setenv(CASE_ROOT_ENV, str(tmp_path))
        (tmp_path / "auto-v0").mkdir()
        assert case_set_dir("auto-v0") == tmp_path / "auto-v0"


class TestTheInstrumentCheckDoesNotNeedAGitCheckout:
    """RP-33. `test_instrument.py` shells out to `git` and needs a real `.git`; in a tarball or
    a `git archive` export it failed on `CalledProcessError` rather than skipping."""

    def test_the_revision_check_skips_rather_than_erroring_without_git(self, tmp_path, monkeypatch):
        from aibench.provenance import git_revision

        copy_root = tmp_path / "no-git"
        (copy_root / "src").mkdir(parents=True)
        shutil.copytree(ROOT / "src" / "aibench", copy_root / "src" / "aibench")
        import aibench.provenance as provenance

        monkeypatch.setattr(provenance, "repo_root", lambda: copy_root)
        assert git_revision() == "unknown-worktree"

    def test_this_checkout_has_git_so_the_suite_is_asserting_the_real_thing(self):
        assert (
            subprocess.run(
                ["git", "rev-parse", "--show-toplevel"],
                cwd=ROOT,
                capture_output=True,
                check=False,
            ).returncode
            == 0
        ), "run the suite from a git checkout"
