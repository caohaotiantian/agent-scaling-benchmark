"""Trace mining and the tier it suggests."""

from aibench.extract.history_parse import normalize_messages
from aibench.extract.trace_signals import (
    TraceSignals,
    signals_from_messages,
    suggest_tier,
)


def _call(name, **args):
    return {"function": {"name": name, "arguments": args}}


def test_one_shot_session_has_no_process_evidence():
    msgs = normalize_messages(
        [
            {"role": "user", "content": "把 add() 改成返回 a+b"},
            {"role": "assistant", "content": "done"},
        ]
    )
    sig = signals_from_messages(msgs)
    assert sig.tool_calls == 0
    assert suggest_tier(sig)[0] == "T1"


def test_read_and_search_suggests_localization_tier():
    msgs = normalize_messages(
        [
            {"role": "user", "content": "接口报 422"},
            {"role": "assistant", "content": "", "tool_calls": [_call("grep", pattern="422")]},
            {"role": "tool", "content": "registry.py:14"},
            {"role": "assistant", "content": "", "tool_calls": [_call("read", path="registry.py")]},
            {"role": "tool", "content": "<path>registry.py</path>"},
        ]
    )
    sig = signals_from_messages(msgs)
    assert sig.search_ops == 1
    assert sig.read_ops == 1
    assert suggest_tier(sig)[0] == "T2"


def test_failing_test_run_suggests_hidden_spec_tier():
    msgs = normalize_messages(
        [
            {"role": "user", "content": "跑不过"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [_call("bash", command="python -m pytest -q")],
            },
            {"role": "tool", "content": "E   AssertionError: assert 0 == 3\n1 failed"},
        ]
    )
    sig = signals_from_messages(msgs)
    assert sig.test_runs == 1
    assert sig.error_signals == 1
    assert suggest_tier(sig)[0] == "T3"


def test_multi_file_edits_suggest_cross_file_tier():
    msgs = normalize_messages(
        [
            {"role": "user", "content": "把 v4 全换成 v5"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    _call("edit", path="client.py"),
                    _call("edit", path="routes.py"),
                    _call("edit", path="config.py"),
                ],
            },
        ]
    )
    sig = signals_from_messages(msgs)
    assert sig.files_touched == 3
    assert suggest_tier(sig)[0] == "T4"


def test_repeated_repair_cycles_suggest_iteration_tier():
    calls = [
        _call("bash", command="pytest -q"),
        _call("edit", path="a.py"),
        _call("bash", command="pytest -q"),
        _call("edit", path="b.py"),
        _call("bash", command="pytest -q"),
        _call("edit", path="c.py"),
    ]
    msgs = normalize_messages(
        [{"role": "assistant", "content": "", "tool_calls": [c]} for c in calls]
    )
    sig = signals_from_messages(msgs)
    assert sig.repair_rounds == 2
    assert sig.files_touched == 3
    assert suggest_tier(sig)[0] == "T5"


def test_repair_rounds_ignores_edits_before_any_test_run():
    msgs = normalize_messages(
        [
            {"role": "assistant", "content": "", "tool_calls": [_call("edit", path="a.py")]},
            {"role": "assistant", "content": "", "tool_calls": [_call("edit", path="b.py")]},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [_call("bash", command="pytest -q")],
            },
        ]
    )
    assert signals_from_messages(msgs).repair_rounds == 0


def test_tool_arguments_may_arrive_as_a_json_string():
    msgs = normalize_messages(
        [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {"function": {"name": "edit", "arguments": '{"file_path": "deep/mod.py"}'}}
                ],
            }
        ]
    )
    sig = signals_from_messages(msgs)
    assert sig.edit_ops == 1
    assert sig.files_touched == 1


def test_non_test_shell_commands_are_not_test_runs():
    msgs = normalize_messages(
        [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [_call("bash", command="git status")],
            }
        ]
    )
    sig = signals_from_messages(msgs)
    assert sig.exec_ops == 1
    assert sig.test_runs == 0


def test_signals_round_trip_to_dict():
    d = TraceSignals(read_ops=2).to_dict()
    assert d["read_ops"] == 2
    assert "repair_rounds" in d
