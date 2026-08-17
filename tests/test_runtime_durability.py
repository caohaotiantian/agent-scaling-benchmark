"""The run loop survives its own failures, and what it writes is either whole or absent.

Every test here fails at `982a9c4` and is the regression half of one finding in
`docs/AUDIT-2026-08-17.md` Part C.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from aibench.ablation import run_ablation
from aibench.io_util import repo_root, write_json, write_jsonl
from aibench.models import Case
from aibench.parallel_util import parallel_map
from aibench.report import build_summary
from aibench.runner import _run_one_attempt

CASE = Case.from_dict(
    {
        "case_id": "durable-clamp",
        "schema_version": "0.1",
        "task_type": "bugfix",
        "language": "python",
        "prompt": "clamp() ignores its bounds. Make it respect lo and hi.",
        "context": {"files": [{"path": "clamp.py", "content": "def clamp(x):\n    return x\n"}]},
        "grader": {"mode": "script", "command": "python -m pytest -q"},
        "metadata": {},
    }
)


class TestTheGraderCannotDiscardARun:
    """NF-04. `grade_case` was the only unguarded stage; one raising future propagated out of
    the executor and `results.jsonl`, `summary.json` and `report.md` were never written."""

    def test_a_raising_grader_becomes_an_infra_error_row(self, tmp_path, monkeypatch):
        import aibench.runner as runner

        def _boom(case, workspace):
            raise IsADirectoryError("the agent created a directory at the hidden test's name")

        monkeypatch.setattr(runner, "grade_case", _boom)
        row = _run_one_attempt(
            CASE,
            case_dir=tmp_path / "c",
            cs="seed-v0",
            agent_cfg=_mock_agent(),
            model_cfg=_mock_model(),
            max_steps=1,
            max_wall_time_s=5,
            case_retries=1,
        )
        assert row["infra_error"] is True
        assert row["passed"] is False
        assert "IsADirectoryError" in row["grade"]["detail"]

    def test_a_non_utf8_gold_file_does_not_raise(self, tmp_path):
        from aibench.grading import grade_case

        case = Case.from_dict(
            {
                "case_id": "gold-bytes",
                "schema_version": "0.1",
                "task_type": "bugfix",
                "language": "python",
                "prompt": "make it right",
                "context": {"files": [{"path": "impl.py", "content": "x = 1\n"}]},
                "grader": {
                    "mode": "gold",
                    "match": "normalized",
                    "gold_files": [{"path": "impl.py", "content": "x = 2\n"}],
                },
                "metadata": {},
            }
        )
        ws = tmp_path / "ws"
        ws.mkdir()
        (ws / "impl.py").write_bytes(b"x = \xff\xfe\n")
        assert grade_case(case, ws).passed is False  # no UnicodeDecodeError

    def test_a_hidden_test_path_occupied_by_a_symlink_is_replaced_not_followed(self, tmp_path):
        from aibench.grading import inject_hidden_tests

        case = Case.from_dict(
            {
                "case_id": "symlinked-hidden",
                "schema_version": "0.1",
                "task_type": "bugfix",
                "language": "python",
                "prompt": "make it right",
                "context": {"files": [{"path": "impl.py", "content": "x = 1\n"}]},
                "grader": {
                    "mode": "script",
                    "command": "python -m pytest -q",
                    "hidden_tests": [{"path": "impl_spec.py", "content": "def test_x(): pass\n"}],
                },
                "metadata": {},
            }
        )
        ws = tmp_path / "ws"
        ws.mkdir()
        outside = tmp_path / "outside.py"
        outside.write_text("untouched\n", encoding="utf-8")
        (ws / "impl_spec.py").symlink_to(outside)

        inject_hidden_tests(case, ws)
        assert outside.read_text(encoding="utf-8") == "untouched\n"
        assert (ws / "impl_spec.py").read_text(encoding="utf-8").startswith("def test_x")


def _mock_agent():
    from aibench.io_util import load_yaml
    from aibench.models import AgentConfig

    return AgentConfig.from_dict(load_yaml(repo_root() / "tests/fixtures/configs/agents/mock.yaml"))


def _mock_model():
    from aibench.io_util import load_yaml
    from aibench.models import ModelConfig

    return ModelConfig.from_dict(
        load_yaml(repo_root() / "tests/fixtures/configs/models/mock-model.yaml")
    )


class TestArtifactsAreWholeOrAbsent:
    """NF-07. `path.open("w")` truncates first, so a kill between truncate and flush left a
    prefix that every reader here treats as a complete file."""

    _KILL_MID_WRITE = """
import json, os, signal, sys
sys.path.insert(0, {src!r})
from aibench.io_util import write_json

payload = {{"rows": ["x" * 4000 for _ in range(400)]}}
target = {target!r}
pid = os.getpid()

import aibench.io_util as io_util
real_dump = json.dump

def dump_then_die(obj, fp, **kw):
    real_dump(obj, fp, **kw)
    fp.flush()
    os.kill(pid, signal.SIGKILL)

json.dump = dump_then_die
write_json(__import__("pathlib").Path(target), payload)
"""

    def test_a_kill_mid_write_leaves_the_previous_file_intact(self, tmp_path):
        target = tmp_path / "case.json"
        write_json(target, {"case_id": "original"})

        script = tmp_path / "killer.py"
        script.write_text(
            self._KILL_MID_WRITE.format(src=str(repo_root() / "src"), target=str(target)),
            encoding="utf-8",
        )
        proc = subprocess.run([sys.executable, str(script)], capture_output=True, text=True)
        assert proc.returncode != 0, "the child was supposed to die mid-write"

        # The old contents survive, and nothing that a reader globs for is left behind.
        assert json.loads(target.read_text(encoding="utf-8")) == {"case_id": "original"}
        assert sorted(p.name for p in tmp_path.glob("*.json")) == ["case.json"]

    def test_the_scratch_file_is_never_mistaken_for_a_case(self, tmp_path):
        """Readers glob `*.json` and skip only names starting with `_`."""
        from aibench.io_util import atomic_write

        seen: list[str] = []
        with atomic_write(tmp_path / "c.json") as handle:
            seen.extend(p.name for p in tmp_path.iterdir())
            handle.write("{}")
        assert [n for n in seen if n.endswith(".json")] == []

    def test_a_report_is_written_atomically_too(self, tmp_path):
        from aibench.io_util import write_text

        target = tmp_path / "report.md"
        write_text(target, "# first\n")
        write_text(target, "# second\n")
        assert target.read_text(encoding="utf-8") == "# second\n"
        assert list(tmp_path.iterdir()) == [target]

    def test_a_torn_case_from_an_older_build_is_not_counted_as_written(self, tmp_path):
        """NF-07's second half: `_load` used `is_file()`, so a truncated file resumed as done."""
        from aibench.checkpoint import JOURNAL_NAME, CaseSink

        (tmp_path / "c1.json").write_text('{"case_id": "c1", "prom', encoding="utf-8")
        (tmp_path / JOURNAL_NAME).write_text(
            json.dumps({"draft": "d1", "status": "written", "case_id": "c1"}) + "\n",
            encoding="utf-8",
        )
        sink = CaseSink(tmp_path, max_cases=5, resume=True)
        assert sink.written == 0
        assert "c1" not in sink.written_ids


class TestParallelAgreesWithSerial:
    """NF-09. The parallel branch filtered `None` out and the serial branch did not."""

    def test_none_results_survive_at_every_worker_count(self):
        items = list(range(6))

        def half(n: int) -> int | None:
            return None if n % 2 else n

        assert parallel_map(half, items, workers=1) == parallel_map(half, items, workers=4)
        assert parallel_map(half, items, workers=4) == [0, None, 2, None, 4, None]


class TestElapsedTimeIsNotASumOfConcurrentIntervals:
    """NF-05. Summing per-case agent clocks overstates elapsed time by up to `case_workers`."""

    def test_the_agent_sum_is_reported_under_its_own_name(self):
        rows = [
            {"case_id": "a", "passed": True, "wall_time_s": 3600.0},
            {"case_id": "b", "passed": True, "wall_time_s": 3600.0},
        ]
        summary = build_summary(
            run_id="r", run_manifest={}, case_results=rows, elapsed_wall_s=3600.0
        )
        assert summary["total_wall_time_h"] == pytest.approx(1.0)
        assert summary["total_agent_wall_time_h"] == pytest.approx(2.0)
        assert summary["throughput_cases_per_h"] == pytest.approx(2.0)

    def test_unmeasured_elapsed_time_is_absent_rather_than_wrong(self):
        rows = [{"case_id": "a", "passed": True, "wall_time_s": 3600.0}]
        summary = build_summary(run_id="r", run_manifest={}, case_results=rows)
        assert summary["total_wall_time_h"] is None
        assert summary["total_agent_wall_time_h"] == pytest.approx(1.0)
        assert "total_wall_time_h" in summary, "the key stays; only its honesty changed"

    def test_a_real_run_records_when_it_started_and_finished(self, tmp_path):
        from aibench.io_util import load_json
        from aibench.runner import run_benchmark

        run_dir = run_benchmark(
            run_config_path=repo_root() / "tests/fixtures/configs/runs/baseline.mock.yaml",
            case_set="seed-v0",
            run_id="elapsed-mock",
            output_root=tmp_path,
        )
        manifest = load_json(run_dir / "run_manifest.json")
        assert manifest["started_at"] and manifest["finished_at"]
        assert manifest["elapsed_wall_time_s"] >= 0
        assert load_json(run_dir / "summary.json")["total_wall_time_h"] is not None


class TestTheMatrixConcurrencyKeyIsRead:
    """NF-08. `parallel or matrix.get("parallel")` short-circuited on a CLI default of 1, so
    11 of 12 shipped matrices declared `parallel: 3` and every one of them ran serially."""

    def test_the_matrix_value_reaches_the_run(self, tmp_path):
        from aibench.io_util import load_json, load_yaml

        mock = repo_root() / "tests/fixtures/configs/runs/ablation-matrix.mock.yaml"
        matrix = load_yaml(mock)
        matrix["parallel"] = 2
        local = tmp_path / "matrix.yaml"
        local.write_text(json.dumps(matrix), encoding="utf-8")

        abl = run_ablation(local, output_root=tmp_path / "runs", allow_weak_grader=True)
        assert load_json(abl / "ablation_summary.json")["parallel"] == 2

    def test_an_explicit_flag_still_wins(self, tmp_path):
        from aibench.io_util import load_json, load_yaml

        mock = repo_root() / "tests/fixtures/configs/runs/ablation-matrix.mock.yaml"
        matrix = load_yaml(mock)
        matrix["parallel"] = 3
        local = tmp_path / "matrix.yaml"
        local.write_text(json.dumps(matrix), encoding="utf-8")

        abl = run_ablation(local, output_root=tmp_path / "runs", allow_weak_grader=True, parallel=1)
        assert load_json(abl / "ablation_summary.json")["parallel"] == 1


class TestNothingWipesTheCheckout:
    """`docs/SESSION-2026-08-14.md` §5.4: a worktree lost every tracked directory except
    `benchmarks/` to a test run, and the cause was never located."""

    def test_materializing_into_the_checkout_is_refused(self):
        from aibench.workspace import assert_disposable

        with pytest.raises(ValueError, match="refusing to wipe"):
            assert_disposable(repo_root())

    def test_materializing_into_a_parent_of_the_checkout_is_refused(self):
        from aibench.workspace import assert_disposable

        with pytest.raises(ValueError, match="refusing to wipe"):
            assert_disposable(repo_root().parent)

    def test_an_ordinary_workspace_is_allowed(self, tmp_path):
        from aibench.workspace import assert_disposable

        assert_disposable(tmp_path / "ws")  # does not raise

    def test_the_run_loop_refuses_a_repo_shaped_workspace(self):
        from aibench.workspace import materialize_workspace

        with pytest.raises(ValueError, match="refusing to wipe"):
            materialize_workspace(CASE, repo_root(), allow_network=False)


def test_write_jsonl_is_atomic_too(tmp_path: Path):
    target = tmp_path / "results.jsonl"
    write_jsonl(target, [{"a": 1}])
    write_jsonl(target, [{"a": 2}, {"b": 3}])
    assert [json.loads(x) for x in target.read_text(encoding="utf-8").splitlines()] == [
        {"a": 2},
        {"b": 3},
    ]
    assert list(tmp_path.iterdir()) == [target]


def test_no_scratch_file_survives_a_failed_write(tmp_path: Path):
    from aibench.io_util import atomic_write

    with pytest.raises(RuntimeError), atomic_write(tmp_path / "x.json"):
        raise RuntimeError("writer blew up")
    assert list(tmp_path.iterdir()) == []
    assert not (tmp_path / "x.json").exists()


def test_the_environment_still_has_a_bare_python():
    """Not a fixture assertion — a note for whoever reads a red suite: the graders shell out."""
    import shutil

    assert shutil.which("python"), "run the suite through `uv run pytest`"
    assert os.environ.get("PATH")
