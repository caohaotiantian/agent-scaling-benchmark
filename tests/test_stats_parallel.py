from pathlib import Path

from aibench.io_util import load_json
from aibench.runner import run_benchmark
from aibench.stats import format_wilson_ci, wilson_ci


def test_wilson_ci_bounds():
    lo, hi = wilson_ci(7, 8)
    assert 0 <= lo < 0.9 < hi <= 1
    s = format_wilson_ci(7, 8)
    assert s and s.startswith("[")


def test_parallel_matches_serial_on_seed(tmp_path: Path):
    r1 = run_benchmark(
        case_set="seed-v0",
        run_id="par-serial",
        output_root=tmp_path / "a",
        case_workers=1,
    )
    r2 = run_benchmark(
        case_set="seed-v0",
        run_id="par-parallel",
        output_root=tmp_path / "b",
        case_workers=2,
    )
    s1 = load_json(r1 / "summary.json")
    s2 = load_json(r2 / "summary.json")
    assert s1["success_rate"] == s2["success_rate"]
    assert s1["success_count"] == s2["success_count"]
    assert s1.get("confidence_interval")
    assert s1.get("stratified_by_task_type")
    assert "case_set_fingerprint" in s1
