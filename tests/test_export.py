from pathlib import Path

from aibench.export_results import export_ablation_csv
from aibench.io_util import write_json


def test_export_csv(tmp_path: Path):
    abl = tmp_path / "abl"
    abl.mkdir()
    write_json(
        abl / "ablation_summary.json",
        {
            "runs": [
                {
                    "experiment_name": "a",
                    "run_id": "r1",
                    "algorithm_name": "Baseline",
                    "agent_name": "mock",
                    "main_model": "m",
                    "case_count": 3,
                    "success_rate": 0.5,
                    "success_count": 1,
                    "total_tokens": 10,
                    "total_cost": 0.01,
                    "total_wall_time_h": 0.1,
                    "relative_success_lift": 0.0,
                    "run_dir": str(abl),
                }
            ]
        },
    )
    out = export_ablation_csv(abl)
    text = out.read_text(encoding="utf-8")
    assert "experiment_name" in text
    assert "Baseline" in text
