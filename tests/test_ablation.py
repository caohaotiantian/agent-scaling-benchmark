from pathlib import Path

import pytest

from aibench.ablation import run_ablation
from aibench.io_util import load_json, repo_root


def test_ablation_fixture_matrix(tmp_path: Path):
    # Production configs are non-mock; unit test uses fixtures only.
    matrix = repo_root() / "tests/fixtures/configs/runs/ablation-matrix.mock.yaml"
    abl_dir = run_ablation(matrix, output_root=tmp_path, allow_weak_grader=True)
    summary = load_json(abl_dir / "ablation_summary.json")
    assert len(summary["runs"]) == 2
    report = (abl_dir / "ablation_report.md").read_text(encoding="utf-8")
    assert "项目效果综述表" in report
    assert report.count("\n| ") >= 3  # header + separator + rows


def test_one_failing_experiment_does_not_void_the_others(tmp_path: Path, monkeypatch):
    """A matrix is hours of paid work; a bad gateway on the last row must not discard the rest."""
    import aibench.ablation as ablation

    original_run = ablation.run_benchmark

    def flaky_run(**kwargs):
        if kwargs.get("run_id") == "abl-mock-b":
            raise RuntimeError("gateway exploded")
        return original_run(**kwargs)

    monkeypatch.setattr(ablation, "run_benchmark", flaky_run)
    matrix = repo_root() / "tests/fixtures/configs/runs/ablation-matrix.mock.yaml"
    abl_dir = ablation.run_ablation(matrix, output_root=tmp_path, allow_weak_grader=True)

    summary = load_json(abl_dir / "ablation_summary.json")
    assert [r["experiment_name"] for r in summary["runs"]] == ["baseline-mock-a"]
    assert [r["experiment_name"] for r in summary["failed_runs"]] == ["baseline-mock-b"]
    report = (abl_dir / "ablation_report.md").read_text(encoding="utf-8")
    assert "执行失败" in report
    assert "gateway exploded" in report


def test_every_experiment_failing_is_still_an_error(tmp_path: Path, monkeypatch):
    import aibench.ablation as ablation

    monkeypatch.setattr(
        ablation, "run_benchmark", lambda **k: (_ for _ in ()).throw(RuntimeError("down"))
    )
    matrix = repo_root() / "tests/fixtures/configs/runs/ablation-matrix.mock.yaml"
    with pytest.raises(RuntimeError, match="every ablation experiment failed"):
        ablation.run_ablation(matrix, output_root=tmp_path, allow_weak_grader=True)
