from aibench.diagnostics import aggregate_failures, render_failures_md


def test_aggregate_failures():
    rows = [
        {"case_id": "a", "passed": False, "failure_category": "模型推理失败", "agent_status": "completed", "grade": {"detail": "exit=1\nfail"}},
        {"case_id": "b", "passed": True, "agent_status": "completed"},
        {"case_id": "c", "passed": False, "failure_category": "Agent协议失败", "agent_status": "failed", "error_message": "parse model output failed"},
    ]
    agg = aggregate_failures(rows)
    assert agg["failed_count"] == 2
    assert "模型推理失败" in agg["by_category"]
    md = render_failures_md(agg)
    assert "失败诊断" in md
