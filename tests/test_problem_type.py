"""Closed-vocab defect-mechanism labels on generated cases."""

from __future__ import annotations

from pathlib import Path

from aibench.cli import main
from aibench.extract.generate_case import heuristic_case_from_draft
from aibench.extract.problem_type import (
    PROBLEM_TYPES,
    classify_problem_type,
    distribution,
    stamp_problem_type,
)
from aibench.extract.reverse_case import reverse_case_from_versions
from aibench.io_util import load_json, write_json
from aibench.report import build_summary


def _case(stub: str, gold: str, *, prompt: str = "it misbehaves", path: str = "mod.py") -> dict:
    return {
        "case_id": "c",
        "schema_version": "0.1",
        "task_type": "bugfix",
        "language": "python",
        "prompt": prompt,
        "context": {
            "files": [
                {"path": path, "content": stub, "role": "impl"},
                {
                    "path": "test_mod.py",
                    "content": "def test_ok():\n    assert True\n",
                    "role": "test",
                },
            ]
        },
        "grader": {
            "mode": "script",
            "command": "python -m pytest -q",
            "gold_files": [{"path": path, "content": gold}],
        },
        "metadata": {},
    }


def test_vocabulary_is_closed_and_ordered():
    assert "other" in PROBLEM_TYPES
    assert PROBLEM_TYPES[-1] == "other"
    assert len(PROBLEM_TYPES) == len(set(PROBLEM_TYPES))


def test_schema_enum_matches_the_code():
    from aibench.cases import case_schema_path

    schema = load_json(case_schema_path())
    enum = schema["properties"]["metadata"]["properties"]["problem_type"]["enum"]
    assert enum == list(PROBLEM_TYPES)


def test_missing_cli_wiring():
    stub = (
        "import argparse\n"
        "def main():\n"
        "    p = argparse.ArgumentParser()\n"
        "    p.add_argument('root')\n"
        "    args = p.parse_args()\n"
        "    walk(args.root)\n"
    )
    gold = (
        "import argparse\n"
        "def main():\n"
        "    p = argparse.ArgumentParser()\n"
        "    p.add_argument('root')\n"
        "    p.add_argument('--exclude-dirs', default='')\n"
        "    args = p.parse_args()\n"
        "    walk(args.root, extra_skip=args.exclude_dirs)\n"
    )
    assert classify_problem_type(_case(stub, gold)).problem_type == "missing_cli_wiring"


def test_off_by_one():
    stub = "def nsum(n):\n    return sum(range(1, n))\n"
    gold = "def nsum(n):\n    return sum(range(1, n + 1))\n"
    assert classify_problem_type(_case(stub, gold)).problem_type == "off_by_one"


def test_wrong_predicate():
    stub = "def keep(x):\n    return not x\n"
    gold = "def keep(x):\n    return x\n"
    assert classify_problem_type(_case(stub, gold)).problem_type == "wrong_predicate"


def test_missing_guard():
    stub = "def average(nums):\n    return sum(nums) / len(nums)\n"
    gold = (
        "def average(nums):\n"
        "    if not nums:\n"
        "        return 0.0\n"
        "    return sum(nums) / len(nums)\n"
    )
    assert classify_problem_type(_case(stub, gold)).problem_type == "missing_guard"


def test_missing_branch():
    stub = "def kind(x):\n    if x < 0:\n        return 'neg'\n    return 'pos'\n"
    gold = (
        "def kind(x):\n"
        "    if x < 0:\n"
        "        return 'neg'\n"
        "    elif x == 0:\n"
        "        return 'zero'\n"
        "    return 'pos'\n"
    )
    assert classify_problem_type(_case(stub, gold)).problem_type == "missing_branch"


def test_normalize_transform():
    stub = "def key(s):\n    return s\n"
    gold = "def key(s):\n    return s.strip().lower()\n"
    assert classify_problem_type(_case(stub, gold)).problem_type == "normalize_transform"


def test_wrong_path_base():
    stub = "import os\nROOT = os.path.dirname(__file__)\n"
    gold = "import os\nROOT = os.path.dirname(os.path.dirname(__file__))\n"
    assert classify_problem_type(_case(stub, gold)).problem_type == "wrong_path_base"


def test_missing_field():
    stub = "from dataclasses import dataclass\n@dataclass\nclass Row:\n    name: str\n"
    gold = (
        "from dataclasses import dataclass\n"
        "@dataclass\n"
        "class Row:\n"
        "    name: str\n"
        "    meet_requirements: bool = False\n"
    )
    assert classify_problem_type(_case(stub, gold)).problem_type == "missing_field"


def test_missing_symbol():
    stub = "def a():\n    return 1\n"
    gold = "def a():\n    return 1\n\ndef calculate_tensor_memory(shape):\n    return 0\n"
    assert classify_problem_type(_case(stub, gold)).problem_type == "missing_symbol"


def test_registry_omission():
    stub = "VIEWS = {\n    'list': ListView,\n}\n"
    gold = "VIEWS = {\n    'list': ListView,\n    'data': DataView,\n}\n"
    assert classify_problem_type(_case(stub, gold)).problem_type == "registry_omission"


def test_wrong_literal():
    stub = "EXT = '.hyper-designer'\n"
    gold = "EXT = '.adtspec'\n"
    assert classify_problem_type(_case(stub, gold)).problem_type == "wrong_literal"


def test_no_gold_falls_back_to_other_or_prompt():
    case = _case("def a():\n    return 1\n", "def a():\n    return 1\n")
    case["grader"]["gold_files"] = []
    assert classify_problem_type(case).problem_type == "other"
    case["prompt"] = "empty list raises ZeroDivisionError"
    assert classify_problem_type(case).problem_type == "missing_guard"


def test_test_files_and_distractors_are_ignored():
    case = _case(
        "def a():\n    return 1\n",
        "def a():\n    return 1\n",
    )
    case["context"]["files"].append({"path": "noise.py", "content": "x=1\n", "role": "distractor"})
    case["grader"]["gold_files"] = [
        {"path": "test_mod.py", "content": "def test_ok():\n    assert False\n"}
    ]
    # No impl/gold pair remains.
    assert classify_problem_type(case).problem_type == "other"


def test_stamp_writes_metadata_and_does_not_fail():
    case = _case(
        "def nsum(n):\n    return sum(range(1, n))\n",
        "def nsum(n):\n    return sum(range(1, n + 1))\n",
    )
    result = stamp_problem_type(case)
    assert result.problem_type == "off_by_one"
    assert case["metadata"]["problem_type"] == "off_by_one"
    assert case["metadata"]["problem_type_source"] == "heuristic"
    assert case["metadata"]["problem_type_reasons"]


def test_generate_cases_prints_problem_type_distribution(tmp_path, capsys):
    drafts = tmp_path / "drafts"
    drafts.mkdir()
    write_json(
        drafts / "d.json",
        _case(
            "def nsum(n):\n    return sum(range(1, n))\n",
            "def nsum(n):\n    return sum(range(1, n + 1))\n",
        ),
    )
    out = tmp_path / "cases"
    rc = main(
        [
            "generate-cases",
            "--input-dir",
            str(drafts),
            "--output-dir",
            str(out),
            "--heuristic-only",
            "--max-cases",
            "5",
        ]
    )
    assert rc == 0
    printed = capsys.readouterr().out
    assert "problem_type distribution:" in printed
    written = load_json(next(out.glob("*.json")))
    assert written["metadata"]["problem_type"] in PROBLEM_TYPES


def test_heuristic_generation_stamps_problem_type():
    draft = _case(
        "def nsum(n):\n    return sum(range(1, n))\n",
        "def nsum(n):\n    return sum(range(1, n + 1))\n",
    )
    case = heuristic_case_from_draft(draft, tier="T1")
    assert case["metadata"]["problem_type"] in PROBLEM_TYPES


def test_reverse_generation_stamps_problem_type():
    import json

    pre = "def nsum(n):\n    return sum(range(1, n))\n"
    post = "def nsum(n):\n    return sum(range(1, n + 1))\n"
    case = reverse_case_from_versions(
        {
            "path": "pkg/nsum.py",
            "pre": pre,
            "post": post,
            "edits": 1,
            "pre_origin": "read_complete",
        },
        draft={"case_id": "db-1", "prompt": "the last value is missing", "metadata": {}},
        chat=lambda _m: json.dumps(
            {
                "prompt": "The last integer in the range is not included in the total.",
                "test_path": "test_nsum.py",
                "test_content": "def test_n():\n    assert True\n",
            }
        ),
    )
    assert case["metadata"]["problem_type"] == "off_by_one"


def test_classify_cases_cli_writes_report_and_annotates(tmp_path: Path, capsys, monkeypatch):
    monkeypatch.setenv("AIBENCH_CASE_ROOT", str(tmp_path))
    case_dir = tmp_path / "ptset"
    case_dir.mkdir()
    write_json(
        case_dir / "c.json",
        _case(
            "def nsum(n):\n    return sum(range(1, n))\n",
            "def nsum(n):\n    return sum(range(1, n + 1))\n",
        ),
    )
    report = tmp_path / "pt.json"
    rc = main(
        [
            "classify-cases",
            "--case-set",
            "ptset",
            "--annotate",
            "--report",
            str(report),
        ]
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "problem_type distribution:" in out
    assert "off_by_one=" in out
    rep = load_json(report)
    assert rep["counts"]["off_by_one"] == 1
    stamped = load_json(case_dir / "c.json")
    assert stamped["metadata"]["problem_type"] == "off_by_one"


def test_distribution_counts_unset():
    assert distribution([{"metadata": {}}, {"metadata": {"problem_type": "other"}}]) == {
        "other": 1,
        "unset": 1,
    }


def test_run_summary_stratifies_by_problem_type():
    rows = [
        {
            "case_id": "a",
            "problem_type": "off_by_one",
            "passed": True,
            "infra_error": False,
        },
        {
            "case_id": "b",
            "problem_type": "off_by_one",
            "passed": False,
            "infra_error": False,
        },
        {
            "case_id": "c",
            "problem_type": "missing_symbol",
            "passed": True,
            "infra_error": False,
        },
    ]
    summary = build_summary(
        run_id="r",
        run_manifest={
            "experiment_name": "e",
            "benchmark_name": "AI-Coding-Assist",
            "case_set": "s",
            "agent_name": "mock",
            "main_model": "m",
            "algorithm_name": "a",
        },
        case_results=rows,
        elapsed_wall_s=1.0,
    )
    strata = summary["stratified_by_problem_type"]
    assert strata["off_by_one"]["n"] == 2
    assert strata["off_by_one"]["successes"] == 1
    assert strata["missing_symbol"]["n"] == 1
