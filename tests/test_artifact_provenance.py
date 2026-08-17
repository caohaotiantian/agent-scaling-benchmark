"""An artifact says what produced it, and does not name the machine that did.

Every test here fails at `982a9c4`.
"""

from __future__ import annotations

import json

import pytest

from aibench.io_util import repo_root

ROOT = repo_root()


def _rows(anchor: str, outcomes: dict[str, bool], **extra) -> dict:
    return {
        "anchor": anchor,
        "rows": [
            {"case_id": cid, "passed": ok, "infra_error": False, **extra}
            for cid, ok in outcomes.items()
        ],
    }


class TestACalibrationSaysWhatMeasuredIt:
    """RP-13, the audit's "highest leverage single change". None of the 13 published
    calibrations records `code_version`, `harness_digest`, `python_version` or a timestamp, and
    24 of the 33 run directories behind them — including all 16 `calibration_*` — stamp the
    literal `aibench@0.1.0 / agent@1.0.0`. `README.md:12` warns that adapter defects moved one
    model's pass rate 58 points; a reader holding these files cannot tell which side of which
    fix any number sits on."""

    def test_a_fresh_export_carries_the_execution_identity(self, tmp_path):
        from aibench.calibrate import calibrate_case_set, load_anchor_panel
        from aibench.io_util import load_json

        anchors, _panel = load_anchor_panel(
            ROOT / "tests/fixtures/configs/runs/anchor-panel.mock.yaml"
        )
        cal_dir, _report = calibrate_case_set(
            "seed-v0",
            anchors,
            repeats=1,
            output_root=tmp_path,
            allow_unfit_anchors=True,
        )
        stored = load_json(cal_dir / "calibration.json")
        provenance = stored["provenance"]
        for field in ("code_version", "harness_digest", "python_version", "venv_digest"):
            assert provenance[field], f"{field} missing"
        assert stored["case_set_fingerprint"]

    def test_the_stamp_is_not_a_constant(self):
        """`aibench@0.1.0 / agent@1.0.0` in 148 manifests is what this replaces."""
        from aibench.provenance import environment

        assert environment()["code_version"] != "aibench@0.1.0"


class TestACalibrationCanBeRecomputedFromItself:
    """RP-19 and RP-48. `auto-v0`'s published row requires "只取有参考解的 105 条" and no field
    identified which 105, so the recipe lands on 62.7 / 13.5 / 23.8 against a published
    75.2 / 16.2 / 8.6 and the reader cannot tell whether the docs or their filter is wrong.
    `by_anchor` is a rate with no denominator for the same reason."""

    def test_each_case_says_whether_it_has_a_reference_solution(self):
        from aibench.calibrate import aggregate_calibration

        runs = [
            _rows("weak", {"a": False, "b": True}, has_reference=True),
            _rows("strong", {"a": True, "b": True}, has_reference=True),
        ]
        runs[0]["rows"][1]["has_reference"] = False
        report = aggregate_calibration(runs)
        by_id = {c["case_id"]: c for c in report["cases"]}
        assert by_id["a"]["has_reference"] is True
        assert by_id["b"]["has_reference"] is False

    def test_each_anchor_says_how_many_attempts_it_contributed(self):
        from aibench.calibrate import aggregate_calibration

        runs = [
            _rows("weak", {"a": False}),
            _rows("weak", {"a": True}),
            _rows("strong", {"a": True}),
        ]
        case = aggregate_calibration(runs)["cases"][0]
        assert case["by_anchor_attempts"] == {"weak": 2, "strong": 1}
        assert case["by_anchor"]["weak"] == pytest.approx(0.5)

    def test_the_runner_supplies_the_field_the_export_needs(self, tmp_path):
        from aibench.calibrate import read_result_rows
        from aibench.runner import run_benchmark

        run_dir = run_benchmark(
            run_config_path=ROOT / "tests/fixtures/configs/runs/baseline.mock.yaml",
            case_set="seed-v0",
            output_root=tmp_path,
        )
        rows = read_result_rows(run_dir / "results.jsonl")
        assert rows and all("has_reference" in r for r in rows)


class TestNoArtifactNamesThisMachine:
    """RP-22 and RP-58. Two tracked calibration files embed
    `/Users/deepsky/Documents/projects/agent-scaling-benchmark/runs/...` in `run_dir` and
    结果目录, and every manifest carried `python_executable` and `working_directory`."""

    def test_paths_are_repo_relative(self, tmp_path, monkeypatch):
        """The run lands under a *stand-in* repository root, not the live checkout. Writing a
        real run into `ROOT/runs` to prove a path is relative leaves the suite mutating the
        working tree it is testing — and `runs/` is the one directory nothing cleans up."""
        import aibench.io_util as io_util
        from aibench.io_util import load_json
        from aibench.runner import run_benchmark

        monkeypatch.setattr(io_util, "repo_root", lambda: tmp_path)
        run_dir = run_benchmark(
            run_config_path=ROOT / "tests/fixtures/configs/runs/baseline.mock.yaml",
            case_set="seed-v0",
            output_root=tmp_path / "runs",
        )
        summary = load_json(run_dir / "summary.json")
        for key in ("result_dir", "report_path", "raw_results_path"):
            assert not summary[key].startswith("/"), f"{key} is absolute: {summary[key]}"
            assert summary[key].startswith("runs/"), summary[key]

    def test_a_path_outside_the_repo_is_left_alone(self, tmp_path):
        """Honesty over tidiness: an `--output-root /tmp/...` really is absolute, and rewriting
        it to something repo-relative would name a directory that does not exist."""
        from aibench.io_util import relative_to_repo

        assert relative_to_repo(tmp_path / "x") == str(tmp_path / "x")

    def _scrubber(self):
        """The compiled pattern from the build script, loaded rather than re-declared."""
        import importlib.util
        import sys

        spec = importlib.util.spec_from_file_location(
            "_docs_build_probe", ROOT / "scripts/build_docs_html.py"
        )
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        try:
            spec.loader.exec_module(module)
            return module._HOME_PATH
        finally:
            sys.modules.pop(spec.name, None)

    def test_the_build_script_carries_no_home_path_of_its_own(self):
        """It held another engineer's home directory as a replacement *target* — harmless in
        effect, still a leaked path in a tracked file, and it had stopped matching anything in
        `_src/` long before that was noticed."""
        body = (ROOT / "scripts/build_docs_html.py").read_text(encoding="utf-8")
        assert "lishanni" not in body

    @pytest.mark.parametrize(
        "text",
        [
            "see /Users/someone/code/study/outputs/x.xlsx here",
            r"C:\Users\bob\proj\a.py",
            "/home/engineer/secret/path.txt",
            "<code>/Users/ab/cd</code>",
        ],
    )
    def test_the_scrubber_removes_a_home_path(self, text):
        """Run, not grepped for. The first version of this pattern was spelled
        ``[^\\s\"'<>)]`` inside a raw string, which excludes a backslash and the *letter* `s`
        rather than whitespace — so `/Users/lishanni/...` was cut at the `s` of the account name
        and the remainder published. A test that checked the source for `_HOME_PATH` passed
        throughout."""
        assert "/Users/" not in self._scrubber().sub("<S>", text)
        assert "/home/" not in self._scrubber().sub("<S>", text)
        assert "Users\\" not in self._scrubber().sub("<S>", text)

    @pytest.mark.parametrize("text", ["no path here at all", "relative/path/is/fine.py"])
    def test_the_scrubber_leaves_ordinary_text_alone(self, text):
        assert self._scrubber().sub("<S>", text) == text


class TestARunThatNeverRanSaysSo:
    """RP-60. `aibench run` exited 0 and wrote a complete report when 100% of cases failed on
    missing credentials, so `success_rate: 0.0` read as a capability result."""

    def test_the_exit_code_is_non_zero(self, tmp_path, monkeypatch):
        from aibench import cli

        def _fake_run(**kwargs):
            run_dir = tmp_path / "run"
            run_dir.mkdir(parents=True, exist_ok=True)
            (run_dir / "summary.json").write_text(
                json.dumps(
                    {
                        "success_rate": 0.0,
                        "success_count": 0,
                        "effective_case_count": 0,
                        "case_count": 12,
                        "infra_error_count": 12,
                        "total_tokens": 0,
                        "total_cost": 0.0,
                    }
                ),
                encoding="utf-8",
            )
            return run_dir

        monkeypatch.setattr(cli, "run_benchmark", _fake_run)
        assert cli.main(["run", "--case-set", "seed-v0"]) == 1

    def test_a_real_run_still_exits_zero(self, tmp_path):
        from aibench import cli

        assert (
            cli.main(
                [
                    "run",
                    "--run-config",
                    str(ROOT / "tests/fixtures/configs/runs/baseline.mock.yaml"),
                    "--case-set",
                    "seed-v0",
                    "--output-root",
                    str(tmp_path),
                ]
            )
            == 0
        )


class TestTheGradingEnvironmentPromiseIsChecked:
    """RP-59. `configs/grading-env.yaml` is a declaration the run never verified, so a case
    importing a promised-but-absent package failed at grading and read as difficulty."""

    def test_the_manifest_records_the_verdict(self, tmp_path):
        from aibench.io_util import load_json
        from aibench.runner import run_benchmark

        run_dir = run_benchmark(
            run_config_path=ROOT / "tests/fixtures/configs/runs/baseline.mock.yaml",
            case_set="seed-v0",
            output_root=tmp_path,
        )
        assert "grading_env_unsatisfied" in load_json(run_dir / "run_manifest.json")

    def test_the_strict_flag_aborts(self, tmp_path, monkeypatch):
        import aibench.grading_env as grading_env
        from aibench.runner import run_benchmark

        monkeypatch.setattr(grading_env, "unsatisfied_promises", lambda **_: ["numpy"])
        with pytest.raises(RuntimeError, match="numpy"):
            run_benchmark(
                run_config_path=ROOT / "tests/fixtures/configs/runs/baseline.mock.yaml",
                case_set="seed-v0",
                output_root=tmp_path,
                require_grading_env=True,
            )


class TestAnAdapterRefusesWhatItCannotPose:
    """H13's surviving clauses. `bare_model` pastes one file into one prompt and silently
    dropped every other implementation file, while its config claims axis A4 跨文件一致性 — the
    axis a one-file prompt is least able to exercise. And `llm_judge` took its model from the
    environment and recorded it nowhere."""

    def _case(self, impl_paths: list[str]):
        from aibench.models import Case

        return Case.from_dict(
            {
                "case_id": "multi",
                "schema_version": "0.1",
                "task_type": "bugfix",
                "language": "python",
                "prompt": "Two files disagree about the same constant; make them agree.",
                "context": {
                    "files": [{"path": p, "content": "x = 1\n", "role": "impl"} for p in impl_paths]
                },
                "grader": {"mode": "script", "command": "python -m pytest -q"},
                "metadata": {},
            }
        )

    def _agent(self):
        from aibench.agents.bare_model import BareModelAgent
        from aibench.io_util import load_yaml
        from aibench.models import AgentConfig, ModelConfig

        return BareModelAgent(
            AgentConfig.from_dict(load_yaml(ROOT / "configs/agents/bare_model.yaml")),
            ModelConfig.from_dict(load_yaml(ROOT / "configs/models/glm52.yaml")),
        )

    def test_a_single_file_case_is_posable(self):
        assert self._agent()._prompt(self._case(["a.py"])) is not None

    def test_a_multi_file_case_is_refused_rather_than_truncated(self, tmp_path, monkeypatch):
        # A key must be present, or the run refuses for the *other* infra reason and this test
        # asserts nothing about posability. It used to pass only on a machine with a `.env`.
        monkeypatch.setenv("OPENAI_API_KEY", "not-a-real-key")
        agent = self._agent()
        assert agent._prompt(self._case(["a.py", "b.py"])) is None

        result = agent.run(self._case(["a.py", "b.py"]), tmp_path, max_steps=1, max_wall_time_s=5)
        assert result.status == "infra_error", "not the model's failure; it never saw the case"
        assert "exactly one impl file" in (result.error_message or "")

    def test_the_judge_names_itself_in_its_verdict(self, monkeypatch, tmp_path):
        """Behaviour, not a grep. Asserting the source contains `f"judge={settings["` passes for
        a literal that was moved into a comment, and fails for a correct rewrite.

        This is also `_grade_llm_judge`'s first test of any kind — RP-34 named the whole LLM
        half of `grading.py` as untested and only the chat-record half was covered.
        """
        import json as json_mod

        import httpx

        import aibench.env_config as env_config
        from aibench.grading import grade_case
        from aibench.models import Case

        monkeypatch.setattr(
            env_config,
            "openai_settings",
            lambda: {"api_key": "k", "base_url": "http://judge.invalid/v1", "model": "judge-7"},
        )

        seen: dict = {}

        class _Resp:
            @staticmethod
            def raise_for_status() -> None: ...

            @staticmethod
            def json() -> dict:
                return {
                    "choices": [
                        {
                            "message": {
                                "content": json_mod.dumps(
                                    {"score": 0.9, "passed": True, "reason": "meets the rubric"}
                                )
                            }
                        }
                    ]
                }

        class _Client:
            def __init__(self, **kw) -> None: ...

            def __enter__(self) -> _Client:
                return self

            def __exit__(self, *a) -> None: ...

            def post(self, url, **kw) -> _Resp:
                seen["model"] = kw["json"]["model"]
                return _Resp()

        monkeypatch.setattr(httpx, "Client", _Client)

        (tmp_path / "m.py").write_text("x = 2\n", encoding="utf-8")
        raw = {
            "case_id": "j",
            "task_type": "bugfix",
            "prompt": "make it right",
            "language": "python",
            "context": {"files": [{"path": "m.py", "role": "impl", "content": "x = 1\n"}]},
            "grader": {"mode": "llm_judge", "judge_rubric": "is it right", "judge_threshold": 0.7},
            "metadata": {"tier": "T2"},
        }
        result = grade_case(Case.from_dict(raw), tmp_path)
        assert result.passed is True
        assert seen["model"] == "judge-7", "the configured judge model must be the one called"
        assert "judge=judge-7" in result.detail, (
            f"the verdict must name the model that produced it: {result.detail!r}"
        )


class TestALineNumberGutterIsRecognisedAsAGutter:
    """H12 clause 1 / M19 clause 1. The `^\\s*\\d+:` strip ran per line, unconditionally, so a
    source line beginning with digits and a colon lost its leading token — and the corrupted
    file became a stub or a reference solution."""

    def _read(self, body: str) -> str:
        return f"<path>impl.py</path>\n<type>file</type>\n<content>{body}</content>"

    def test_a_real_gutter_is_stripped(self):
        from aibench.extract.history_parse import extract_files_from_tool_text

        body = "1: def f():\n2:     return 1\n3: \n"
        (found,) = extract_files_from_tool_text(self._read(body))
        assert found["content"] == "def f():\n    return 1\n"

    def test_a_dict_keyed_by_number_survives(self):
        from aibench.extract.history_parse import extract_files_from_tool_text

        body = 'STATUS = {\n    404: "not found",\n    500: "server error",\n}\n'
        (found,) = extract_files_from_tool_text(self._read(body))
        assert '404: "not found"' in found["content"]
        assert '500: "server error"' in found["content"]

    def test_a_non_consecutive_run_is_not_a_gutter(self):
        from aibench.extract.history_parse import extract_files_from_tool_text

        body = "8080: upstream_a\n9090: upstream_b\n"
        (found,) = extract_files_from_tool_text(self._read(body))
        assert "8080: upstream_a" in found["content"]
