import csv
import json
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


def test_a_candidate_that_measured_nothing_gets_no_lift(tmp_path: Path, monkeypatch):
    """`-100.0pp` against a baseline the row never met is a comparison, not a missing value.

    The baseline side already answered None here. The candidate side read its None as a 0% rate,
    so a run with 0 effective cases and every case an infra error was published as a capability
    result. `ablation_report.md` warns about such rows; `ablation_overview.csv` and the XLSX
    carry neither the warning nor an `effective_case_count` column.
    """
    import aibench.ablation as ablation
    from aibench.io_util import write_json, write_jsonl
    from aibench.report import build_summary

    original_run = ablation.run_benchmark

    def nothing_measured_on_b(**kwargs):
        """Rewrite abl-mock-b as the run a broken gateway produces: every case an infra error.

        `results.jsonl` has to move with `summary.json`, or the strata and pass@k tables are
        still computed from four passing cases and the report contradicts its own headline.
        """
        run_dir = original_run(**kwargs)
        if kwargs.get("run_id") != "abl-mock-b":
            return run_dir
        raw = (run_dir / "results.jsonl").read_text(encoding="utf-8").splitlines()
        rows = [
            {**json.loads(ln), "passed": False, "infra_error": True, "agent_status": "infra_error"}
            for ln in raw
            if ln.strip()
        ]
        write_jsonl(run_dir / "results.jsonl", rows)
        summary = build_summary(
            run_id=load_json(run_dir / "summary.json")["run_id"],
            run_manifest=load_json(run_dir / "run_manifest.json"),
            case_results=rows,
            elapsed_wall_s=1.0,
        )
        write_json(run_dir / "summary.json", summary)
        return run_dir

    monkeypatch.setattr(ablation, "run_benchmark", nothing_measured_on_b)
    matrix = repo_root() / "tests/fixtures/configs/runs/ablation-matrix.mock.yaml"
    abl_dir = ablation.run_ablation(matrix, output_root=tmp_path, allow_weak_grader=True)

    rows = {r["experiment_name"]: r for r in load_json(abl_dir / "ablation_summary.json")["runs"]}
    unmeasured = rows["baseline-mock-b"]
    assert unmeasured["success_rate"] is None
    assert unmeasured["relative_success_lift"] is None
    assert unmeasured["overview_row"]["相对基线收益"] is None
    assert unmeasured["overview_row"]["主指标值"] == "-"
    # The row that did measure something is unaffected.
    assert rows["baseline-mock-a"]["relative_success_lift"] == 0.0


def test_an_unmeasured_row_reads_as_unmeasured_in_every_artifact(tmp_path: Path, monkeypatch):
    """`0.0%` beside `有效Case 0` is a rate for a run that never produced one.

    The markdown warns about such rows further down, but the overview table, the Runs table and
    the XLSX are what get quoted, and the XLSX carries no warning at all.
    """
    import aibench.ablation as ablation
    from aibench.export_results import export_ablation_csv, export_ablation_xlsx
    from aibench.io_util import write_json, write_jsonl
    from aibench.report import build_summary

    original_run = ablation.run_benchmark

    def nothing_measured_on_b(**kwargs):
        """Rewrite abl-mock-b as the run a broken gateway produces: every case an infra error.

        `results.jsonl` has to move with `summary.json`, or the strata and pass@k tables are
        still computed from four passing cases and the report contradicts its own headline.
        """
        run_dir = original_run(**kwargs)
        if kwargs.get("run_id") != "abl-mock-b":
            return run_dir
        raw = (run_dir / "results.jsonl").read_text(encoding="utf-8").splitlines()
        rows = [
            {**json.loads(ln), "passed": False, "infra_error": True, "agent_status": "infra_error"}
            for ln in raw
            if ln.strip()
        ]
        write_jsonl(run_dir / "results.jsonl", rows)
        summary = build_summary(
            run_id=load_json(run_dir / "summary.json")["run_id"],
            run_manifest=load_json(run_dir / "run_manifest.json"),
            case_results=rows,
            elapsed_wall_s=1.0,
        )
        write_json(run_dir / "summary.json", summary)
        return run_dir

    monkeypatch.setattr(ablation, "run_benchmark", nothing_measured_on_b)
    matrix = repo_root() / "tests/fixtures/configs/runs/ablation-matrix.mock.yaml"
    abl_dir = ablation.run_ablation(matrix, output_root=tmp_path, allow_weak_grader=True)

    report = (abl_dir / "ablation_report.md").read_text(encoding="utf-8")

    def row_in(section: str, first_cell: str) -> list[str]:
        """Four tables here start a row with the same name; take the one under `section`."""
        body = report.split(f"## {section}", 1)[1].split("\n## ", 1)[0]
        line = next(ln for ln in body.splitlines() if ln.startswith(f"| {first_cell} |"))
        return [c.strip() for c in line.strip().strip("|").split("|")]

    assert row_in("项目效果综述表", "Baseline-repeat")[6] == "-"
    assert row_in("项目效果综述表", "Baseline")[6] == "100.0%"
    assert row_in("Runs", "baseline-mock-b")[2] == "-"
    assert row_in("Runs", "baseline-mock-a")[2] == "1.000"
    # The strata and pass@k tables are computed from the same rows and must agree with them.
    assert row_in("分层成功率（按 tier）", "baseline-mock-b")[1] == "-"
    assert row_in("采样扩展与成本", "baseline-mock-b")[2] == "-"
    # A pair with no shared measured case is not "no significant difference".
    mcnemar = row_in("配对显著性检验（McNemar，相对基线）", "baseline-mock-b")
    assert mcnemar[5] == "-" and mcnemar[6] == "-"

    export_ablation_csv(abl_dir)
    by_name = {
        r["experiment_name"]: r
        for r in csv.DictReader((abl_dir / "ablation_overview.csv").read_text().splitlines())
    }
    assert by_name["baseline-mock-b"]["relative_success_lift"] == ""
    assert by_name["baseline-mock-a"]["relative_success_lift"] == "0.0"

    import openpyxl

    export_ablation_xlsx(abl_dir)
    ws = openpyxl.load_workbook(abl_dir / "ablation_overview.xlsx").active
    headline = {row[0]: row[6] for row in ws.iter_rows(values_only=True)}
    assert headline["Baseline-repeat"] == "-"
    assert headline["Baseline"] == "100.0%"


def test_every_experiment_failing_is_still_an_error(tmp_path: Path, monkeypatch):
    import aibench.ablation as ablation

    monkeypatch.setattr(
        ablation, "run_benchmark", lambda **k: (_ for _ in ()).throw(RuntimeError("down"))
    )
    matrix = repo_root() / "tests/fixtures/configs/runs/ablation-matrix.mock.yaml"
    with pytest.raises(RuntimeError, match="every ablation experiment failed"):
        ablation.run_ablation(matrix, output_root=tmp_path, allow_weak_grader=True)


def test_shared_case_sets_are_filtered_before_any_worker_starts(tmp_path: Path, monkeypatch):
    """Filtering rmtree's and repopulates a shared `.ablation-filtered-<set>` directory. Doing
    it inside a worker means two rows on the same set race, and the loser runs against a
    half-copied case set — a wrong number rather than a crash."""
    import aibench.ablation as ablation

    calls: list[str] = []
    real = ablation._filter_unusable_cases

    def counting(case_set, *, skip_weak, skip_invalid):
        calls.append(case_set)
        return real(case_set, skip_weak=skip_weak, skip_invalid=skip_invalid)

    monkeypatch.setattr(ablation, "_filter_unusable_cases", counting)
    matrix = repo_root() / "tests/fixtures/configs/runs/ablation-matrix.mock.yaml"
    ablation.run_ablation(matrix, output_root=tmp_path, allow_weak_grader=True, parallel=2)

    # Both rows share seed-v0; it must be resolved exactly once, up front.
    assert calls.count("seed-v0") == 1, calls


def test_parallel_rows_produce_the_same_rows_as_serial(tmp_path: Path):
    matrix = repo_root() / "tests/fixtures/configs/runs/ablation-matrix.mock.yaml"
    serial = load_json(
        run_ablation(matrix, output_root=tmp_path / "s", allow_weak_grader=True, parallel=1)
        / "ablation_summary.json"
    )
    par = load_json(
        run_ablation(matrix, output_root=tmp_path / "p", allow_weak_grader=True, parallel=2)
        / "ablation_summary.json"
    )
    assert [r["experiment_name"] for r in serial["runs"]] == [
        r["experiment_name"] for r in par["runs"]
    ], "parallel execution must not reorder the matrix"
    assert [r["success_rate"] for r in serial["runs"]] == [r["success_rate"] for r in par["runs"]]
