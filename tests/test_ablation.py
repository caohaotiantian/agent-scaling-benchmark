from pathlib import Path

from aibench.ablation import run_ablation
from aibench.io_util import load_json, repo_root


def test_ablation_mock_matrix(tmp_path: Path):
    matrix = repo_root() / "configs/runs/ablation-matrix.mock.yaml"
    abl_dir = run_ablation(matrix, output_root=tmp_path)
    summary = load_json(abl_dir / "ablation_summary.json")
    assert len(summary["runs"]) == 2
    report = (abl_dir / "ablation_report.md").read_text(encoding="utf-8")
    assert "项目效果综述表" in report
    assert report.count("\n| ") >= 3  # header + separator + rows
