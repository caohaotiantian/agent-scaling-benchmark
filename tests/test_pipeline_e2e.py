"""End-to-end: production trace → tiered case → calibration verdict. No network."""

from pathlib import Path

from aibench.calibrate import calibrate_case_set, load_anchor_panel
from aibench.cases import load_schema_validator
from aibench.extract.generate_case import heuristic_case_from_draft
from aibench.extract.history_parse import normalize_messages
from aibench.extract.llm_chat_records import ChatRecord, record_to_case_draft
from aibench.extract.trace_signals import signals_from_messages, suggest_tier
from aibench.io_util import repo_root
from aibench.models import Case
from aibench.tiers import check_tier_invariants
from aibench.validity import audit_case

TRACE = [
    {"role": "system", "content": "You are opencode"},
    {
        "role": "user",
        "content": "接口返回的错误码解析不对，某些条目的 message 里混进了描述段落，帮我修一下",
    },
    {
        "role": "assistant",
        "content": "",
        "tool_calls": [{"function": {"name": "grep", "arguments": {"pattern": "error_code"}}}],
    },
    {"role": "tool", "content": "extract.py:12"},
    {
        "role": "assistant",
        "content": "",
        "tool_calls": [{"function": {"name": "read", "arguments": {"path": "extract.py"}}}],
    },
    {
        "role": "tool",
        "content": (
            "<path>extract.py</path>\n<type>file</type>\n"
            "<content>1: import re\n2: def extract(md):\n3:     return []\n</content>"
        ),
    },
    {
        "role": "assistant",
        "content": "",
        "tool_calls": [{"function": {"name": "bash", "arguments": {"command": "pytest -q"}}}],
    },
    {"role": "tool", "content": "E   AssertionError: assert [] == [('E1', 'boom')]\n1 failed"},
    {
        "role": "assistant",
        "content": "",
        "tool_calls": [{"function": {"name": "edit", "arguments": {"path": "extract.py"}}}],
    },
    {
        "role": "assistant",
        "content": "",
        "tool_calls": [{"function": {"name": "bash", "arguments": {"command": "pytest -q"}}}],
    },
    {"role": "tool", "content": "1 passed"},
    {
        "role": "assistant",
        "content": "修好了：\n```python\nimport re\n\n\ndef extract(md):\n    return re.findall(r'(E\\d+): (.+)', md)\n```",
    },
]


def _record() -> ChatRecord:
    return ChatRecord(
        request_id="chatcmpl-e2e-0001",
        start_time=None,
        model="glm-5.2",
        requests_tags=["User-Agent: opencode/1.2.25"],
        tools=[{"function": {"name": "bash"}}, {"function": {"name": "read"}}],
        full_history=TRACE,
        key_alias=None,
    )


def test_trace_process_signals_drive_the_tier_suggestion():
    sig = signals_from_messages(normalize_messages(TRACE))
    assert sig.search_ops == 1
    assert sig.test_runs == 2
    assert sig.repair_rounds == 1
    assert sig.error_signals >= 1
    # Iterating on a failing test is what T3 exists to measure.
    assert suggest_tier(sig)[0] == "T3"


def test_draft_carries_the_trace_evidence_forward():
    draft = record_to_case_draft(_record())
    assert draft is not None
    meta = draft["metadata"]
    assert meta["tier"] == "T3"
    assert meta["tier_source"] == "trace_signals"
    assert meta["trace_signals"]["repair_rounds"] == 1
    assert meta["tier_reasons"]


def test_generated_case_is_schema_valid_and_labelled_honestly():
    """The draft asks for T3; without a reference solution it must not claim one."""
    draft = record_to_case_draft(_record())
    case = heuristic_case_from_draft(draft)
    assert sorted(load_schema_validator().iter_errors(case), key=lambda e: list(e.path)) == []
    assert case["metadata"]["tier_requested"] == "T3"
    assert case["metadata"]["tier"] in {"T1", "T2"}
    assert check_tier_invariants(Case.from_dict(case)).ok is True


def test_audit_gates_run_on_a_generated_case():
    case = Case.from_dict(heuristic_case_from_draft(record_to_case_draft(_record())))
    report = audit_case(case)
    assert "stub_fail" in report.checks
    assert "reference_solution" in report.checks
    assert report.checks["tier"]["ok"] is True


def test_calibration_separates_a_weak_anchor_from_a_strong_one(tmp_path: Path):
    """The full loop: run the panel, measure per-case discrimination, decide keep/drop."""
    anchors, _ = load_anchor_panel(
        repo_root() / "tests/fixtures/configs/runs/anchor-panel.mock.yaml"
    )
    cal_dir, report = calibrate_case_set(
        "seed-v0", anchors, repeats=1, output_root=tmp_path, case_workers=2
    )

    assert (cal_dir / "calibration.json").is_file()
    assert (cal_dir / "calibration_report.md").is_file()
    assert report["run_count"] == 2
    assert report["total_cases"] >= 3

    # The strong anchor solves the seed cases and the weak one never edits, so every case
    # separates the panel perfectly and none is discarded as a giveaway.
    for case in report["cases"]:
        assert case["by_anchor"]["strong-mock"] > case["by_anchor"]["weak-mock"]
        assert case["spread"] == 1.0
        assert case["keep"] is True, case["reasons"]
    assert report["kept_count"] == report["total_cases"]
