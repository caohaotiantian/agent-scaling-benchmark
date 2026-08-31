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


def test_off_by_one_is_wrong_condition():
    stub = "def nsum(n):\n    return sum(range(1, n))\n"
    gold = "def nsum(n):\n    return sum(range(1, n + 1))\n"
    assert classify_problem_type(_case(stub, gold)).problem_type == "wrong_condition"


def test_wrong_predicate_is_wrong_condition():
    stub = "def keep(x):\n    return not x\n"
    gold = "def keep(x):\n    return x\n"
    assert classify_problem_type(_case(stub, gold)).problem_type == "wrong_condition"


def test_missing_guard_is_control_flow():
    stub = "def average(nums):\n    return sum(nums) / len(nums)\n"
    gold = (
        "def average(nums):\n"
        "    if not nums:\n"
        "        return 0.0\n"
        "    return sum(nums) / len(nums)\n"
    )
    assert classify_problem_type(_case(stub, gold)).problem_type == "control_flow"


def test_missing_branch_is_control_flow():
    stub = "def kind(x):\n    if x < 0:\n        return 'neg'\n    return 'pos'\n"
    gold = (
        "def kind(x):\n"
        "    if x < 0:\n"
        "        return 'neg'\n"
        "    elif x == 0:\n"
        "        return 'zero'\n"
        "    return 'pos'\n"
    )
    assert classify_problem_type(_case(stub, gold)).problem_type == "control_flow"


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
    assert classify_problem_type(_case(stub, gold)).problem_type == "schema_gap"


def test_missing_symbol():
    stub = "def a():\n    return 1\n"
    gold = "def a():\n    return 1\n\ndef calculate_tensor_memory(shape):\n    return 0\n"
    assert classify_problem_type(_case(stub, gold)).problem_type == "missing_symbol"


def test_new_class_with_dict_literals_is_missing_symbol_not_schema_gap():
    stub = "def a():\n    return 1\n"
    gold = (
        "def a():\n"
        "    return 1\n\n"
        "class Graph:\n"
        "    def add_edge(self, a, b):\n"
        "        return {'from': a, 'to': b}\n"
    )
    assert classify_problem_type(_case(stub, gold)).problem_type == "missing_symbol"


def test_registry_omission():
    stub = "VIEWS = {\n    'list': ListView,\n}\n"
    gold = "VIEWS = {\n    'list': ListView,\n    'data': DataView,\n}\n"
    assert classify_problem_type(_case(stub, gold)).problem_type == "schema_gap"


def test_wrong_literal():
    stub = "EXT = '.hyper-designer'\n"
    gold = "EXT = '.adtspec'\n"
    assert classify_problem_type(_case(stub, gold)).problem_type == "wrong_literal"


def test_pairwise_is_review_choice_not_wrong_literal():
    case = _case("CHOICE = '?'\n", 'CHOICE = "B"\n', path="answer.py")
    case["task_type"] = "pairwise"
    assert classify_problem_type(case).problem_type == "review_choice"


def test_js_object_method_is_missing_symbol():
    stub = "const api = {\n  get(token) { return 1 }\n}\n"
    gold = (
        "const api = {\n"
        "  get(token) { return 1 },\n"
        "  uploadImage(token, formData) { return fetch('/x') }\n"
        "}\n"
    )
    assert classify_problem_type(_case(stub, gold, path="api.js")).problem_type == "missing_symbol"


def test_ts_union_member_is_schema_gap():
    stub = "type S = 'ENTERING' | 'COMPLETED'\nconst T = { COMPLETED: [] as const }\n"
    gold = (
        "type S = 'ENTERING' | 'COMPLETED' | 'REWORK'\n"
        "const T = { COMPLETED: ['REWORK'] as const }\n"
    )
    assert classify_problem_type(_case(stub, gold, path="life.ts")).problem_type == "schema_gap"


def test_i18n_lines_are_copy_change():
    stub = "log('初始化完成')\nlog('搜索开始')\nlog('类别')\n"
    gold = "log('Initialization complete')\nlog('Search started')\nlog('Category')\n"
    assert classify_problem_type(_case(stub, gold)).problem_type == "copy_change"


def test_translated_message_list_is_copy_change_not_schema_gap():
    stub = "MSGS = ['初始化完成', '搜索开始']\n"
    gold = "MSGS = ['Initialization complete', 'Search started']\n"
    assert classify_problem_type(_case(stub, gold)).problem_type == "copy_change"


def test_dotdot_path_is_wrong_path_base():
    stub = "import os\nP = os.path.join(os.path.dirname(__file__), '..')\n"
    gold = "import os\nP = os.path.join(os.path.dirname(__file__), '..', '..')\n"
    assert classify_problem_type(_case(stub, gold)).problem_type == "wrong_path_base"


def test_wholesale_rewrite_has_its_own_slug():
    stub = "def a():\n    return 1\n" + ("x = 1\n" * 40)
    gold = "def b():\n    return 2\n" + ("y = 2\n" * 40)
    assert classify_problem_type(_case(stub, gold)).problem_type == "rewrite"


def test_no_gold_falls_back_to_other_or_prompt():
    case = _case("def a():\n    return 1\n", "def a():\n    return 1\n")
    case["grader"]["gold_files"] = []
    assert classify_problem_type(case).problem_type == "other"
    case["prompt"] = "empty list raises ZeroDivisionError"
    assert classify_problem_type(case).problem_type == "control_flow"


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


def test_null_metadata_does_not_raise():
    case = _case(
        "def nsum(n):\n    return sum(range(1, n))\n",
        "def nsum(n):\n    return sum(range(1, n + 1))\n",
    )
    case["metadata"] = None
    result = stamp_problem_type(case)
    assert result.problem_type == "wrong_condition"
    assert isinstance(case["metadata"], dict)
    assert case["metadata"]["problem_type"] == "wrong_condition"


def test_malformed_gold_files_do_not_raise():
    case = _case("def a():\n    return 1\n", "def a():\n    return 1\n")
    case["grader"]["gold_files"] = ["oops", {"path": "mod.py"}]
    assert classify_problem_type(case).problem_type in PROBLEM_TYPES
    case["grader"] = "not-an-object"
    assert stamp_problem_type(case).problem_type == "other"
    case["grader"] = {"mode": "script", "gold_files": 1}
    assert stamp_problem_type(case).problem_type == "other"


def test_empty_stub_new_symbol_is_missing_symbol_not_wholesale():
    stub = ""
    gold = "def calculate_tensor_memory(shape):\n    return 0\n"
    assert classify_problem_type(_case(stub, gold)).problem_type == "missing_symbol"


def test_new_function_with_else_is_missing_symbol_not_missing_branch():
    stub = "def a():\n    return 1\n"
    gold = (
        "def a():\n    return 1\n\n"
        "def pick(x):\n"
        "    if x:\n"
        "        return 1\n"
        "    else:\n"
        "        return 0\n"
    )
    assert classify_problem_type(_case(stub, gold)).problem_type == "missing_symbol"


def test_prompt_cli_is_not_a_substring_of_client():
    case = _case("def a():\n    return 1\n", "def a():\n    return 1\n")
    case["grader"]["gold_files"] = []
    case["prompt"] = "The client failed to reconnect."
    assert classify_problem_type(case).problem_type == "other"


def test_gold_only_test_path_is_ignored():
    case = _case("def a():\n    return 1\n", "def a():\n    return 1\n")
    case["grader"]["gold_files"] = [
        {"path": "test_other.py", "content": "def test_ok():\n    assert False\n"}
    ]
    assert classify_problem_type(case).problem_type == "other"


def test_stamp_writes_metadata_and_does_not_fail():
    case = _case(
        "def nsum(n):\n    return sum(range(1, n))\n",
        "def nsum(n):\n    return sum(range(1, n + 1))\n",
    )
    result = stamp_problem_type(case)
    assert result.problem_type == "wrong_condition"
    assert case["metadata"]["problem_type"] == "wrong_condition"
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
    assert case["metadata"]["problem_type"] == "wrong_condition"


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
    assert "wrong_condition=" in out
    rep = load_json(report)
    assert rep["counts"]["wrong_condition"] == 1
    stamped = load_json(case_dir / "c.json")
    assert stamped["metadata"]["problem_type"] == "wrong_condition"


def test_classify_cases_skips_a_broken_file_and_still_labels_the_rest(
    tmp_path: Path, capsys, monkeypatch
):
    monkeypatch.setenv("AIBENCH_CASE_ROOT", str(tmp_path))
    case_dir = tmp_path / "ptmix"
    case_dir.mkdir()
    write_json(
        case_dir / "good.json",
        _case(
            "def nsum(n):\n    return sum(range(1, n))\n",
            "def nsum(n):\n    return sum(range(1, n + 1))\n",
        ),
    )
    (case_dir / "bad.json").write_text("{not json", encoding="utf-8")
    (case_dir / "arr.json").write_text("[1, 2, 3]", encoding="utf-8")
    report = tmp_path / "pt.json"
    rc = main(
        [
            "classify-cases",
            "--case-set",
            "ptmix",
            "--annotate",
            "--report",
            str(report),
        ]
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "wrong_condition=" in out
    assert "skip" in out.lower() or "bad.json" in out
    assert "arr.json" in out
    assert load_json(case_dir / "good.json")["metadata"]["problem_type"] == "wrong_condition"
    assert (case_dir / "bad.json").read_text(encoding="utf-8") == "{not json"
    assert (case_dir / "arr.json").read_text(encoding="utf-8") == "[1, 2, 3]"
    rep = load_json(report)
    assert rep["counts"]["wrong_condition"] == 1


def test_distribution_counts_unknown():
    assert distribution(
        [{"metadata": {}}, {"metadata": {"problem_type": "other"}}, {"metadata": "x"}]
    ) == {
        "other": 1,
        "unknown": 2,
    }


def test_run_summary_stratifies_by_problem_type():
    rows = [
        {
            "case_id": "a",
            "problem_type": "wrong_condition",
            "passed": True,
            "infra_error": False,
        },
        {
            "case_id": "b",
            "problem_type": "wrong_condition",
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
    assert strata["wrong_condition"]["n"] == 2
    assert strata["wrong_condition"]["successes"] == 1
    assert strata["missing_symbol"]["n"] == 1
