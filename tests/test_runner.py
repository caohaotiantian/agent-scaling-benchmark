from pathlib import Path

from aibench.io_util import load_json
from aibench.report import check_summary
from aibench.runner import run_benchmark


def test_mock_run_end_to_end(tmp_path: Path):
    run_dir = run_benchmark(
        case_set="seed-v0",
        run_id="test-mock-001",
        output_root=tmp_path,
    )
    assert (run_dir / "run_manifest.json").is_file()
    assert (run_dir / "summary.json").is_file()
    assert (run_dir / "results.jsonl").is_file()
    assert (run_dir / "report.md").is_file()
    assert (run_dir / "tables.json").is_file()

    summary = load_json(run_dir / "summary.json")
    assert check_summary(summary) == []
    assert summary["benchmark_name"] == "AI-Coding-Assist"
    assert summary["primary_metric_name"] == "task_success_rate"
    assert summary["case_count"] >= 3
    assert summary["success_count"] >= 3
    assert summary["agent_name"] == "mock"
    assert "tables" in summary
    assert "overview_row" in summary["tables"]
    report = (run_dir / "report.md").read_text(encoding="utf-8")
    assert "项目效果综述表" in report
    assert "通用结果总表" in report
