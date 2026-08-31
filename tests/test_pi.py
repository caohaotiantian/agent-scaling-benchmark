"""The second production-agent adapter, and the three things about it that were measured.

pi reports the same usage on four different events, so a parser that adds every usage it sees
inflates the bill three- to fourfold while still passing "tokens are non-zero". pi has no step
budget, so the budget is this adapter's own kill and has to actually stop the run. And pi is
driven by streaming its stdout, which is where a single ``communicate()`` used to keep stderr
drained for free -- an undrained stderr deadlocks the child and files as a wall-clock timeout.
"""

from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest

from aibench.agents.cli_sandbox import escapes_workspace
from aibench.agents.pi import (
    API_KEY_VAR,
    PROVIDER_ID,
    PiAgent,
    build_models_json,
    build_settings_json,
    child_environment,
    parse_events,
)
from aibench.agents.registry import create_agent
from aibench.models import AgentConfig, Case, ModelConfig

CASE = Case.from_dict(
    {
        "case_id": "c1",
        "schema_version": "0.1",
        "task_type": "bugfix",
        "language": "python",
        "prompt": "Values below the lower bound come back unchanged.",
        "context": {
            "files": [
                {
                    "path": "clamp.py",
                    "content": "def clamp(v, lo, hi):\n    return min(v, hi)\n",
                    "role": "impl",
                },
                {
                    "path": "test_clamp.py",
                    "content": "def test_low():\n    assert True\n",
                    "role": "test",
                },
            ]
        },
        "grader": {"mode": "script", "command": "python -m pytest -q", "gold_files": []},
        "metadata": {},
    }
)


def _agent(**opts) -> PiAgent:
    # These tests drive a stand-in binary, not a measurement. The production default is
    # sandbox=True, which refuses where sandbox-exec is absent. Tests that want that refusal
    # pass sandbox=True themselves.
    opts.setdefault("sandbox", False)
    ac = AgentConfig.from_dict({"name": "pi", "version": "1", "adapter": "pi", "options": opts})
    mc = ModelConfig.from_dict(
        {
            "name": "m",
            "provider": "openai_compat",
            "model": "GLM-5.2",
            "base_url": "http://gw.test/v1",
            "api_key_env": "OPENAI_API_KEY",
            "temperature": 0,
            "max_tokens": 8192,
        }
    )
    return PiAgent(ac, mc)


def _workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "workspace"
    ws.mkdir()
    (ws / "clamp.py").write_text("def clamp(v, lo, hi):\n    return min(v, hi)\n")
    (ws / "test_clamp.py").write_text("def test_low():\n    assert True\n")
    return ws


def _fake_cli(tmp_path: Path, body: str) -> str:
    """An executable standing in for pi, so the adapter is testable without a gateway."""
    script = tmp_path / "fake-pi"
    script.write_text("#!/usr/bin/env python3\n" + body)
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return str(script)


def _assistant(usage: dict, *, text: str = "", stop: str = "stop") -> str:
    """One `message_end` line for an assistant message, the only event usage is taken from."""
    return json.dumps(
        {
            "type": "message_end",
            "message": {
                "role": "assistant",
                "stopReason": stop,
                "content": [{"type": "text", "text": text}] if text else [],
                "usage": usage,
            },
        }
    )


def _usage(inp: int, out: int, *, cache_read: int = 0, cache_write: int = 0, reasoning: int = 0):
    return {
        "input": inp,
        "output": out,
        "cacheRead": cache_read,
        "cacheWrite": cache_write,
        "reasoning": reasoning,
        "totalTokens": inp + out + cache_read + cache_write,
    }


# --- generated configuration ----------------------------------------------------------------


def test_the_gateway_comes_from_the_model_config_not_the_operators_machine(monkeypatch):
    monkeypatch.setenv("OPENAI_BASE_URL", "http://operators-own-gateway.test/v1")
    config = build_models_json(
        base_url="http://gw.test/v1",
        model_name="GLM-5.2",
        max_tokens=8192,
        temperature=0,
        context_window=128000,
    )
    provider = config["providers"][PROVIDER_ID]
    assert provider["baseUrl"] == "http://gw.test/v1"
    assert provider["api"] == "openai-completions"
    assert provider["models"][0]["id"] == "GLM-5.2"


def test_the_sampling_the_manifest_records_is_the_sampling_pi_is_given():
    """`runner.py` stamps the model config's temperature and max_tokens into the manifest
    whatever the adapter sent. pi sends no temperature at all to an OpenAI-compatible endpoint
    unless `samplingParams` carries one, and defaults maxTokens to 16384 rather than 8192 --
    so omitting either publishes a run described as something it was not."""
    model = build_models_json(
        base_url="http://gw.test/v1",
        model_name="GLM-5.2",
        max_tokens=8192,
        temperature=0,
        context_window=200000,
    )["providers"][PROVIDER_ID]["models"][0]
    assert model["samplingParams"]["temperature"] == 0
    assert model["maxTokens"] == 8192
    assert model["contextWindow"] == 200000


def test_the_api_key_is_named_not_written_into_the_generated_config():
    """The staging directory survives a hard crash; a secret written into it survives too."""
    serialized = json.dumps(
        build_models_json(
            base_url="http://gw.test/v1",
            model_name="GLM-5.2",
            max_tokens=8192,
            temperature=0,
            context_window=128000,
        )
    )
    assert f"${API_KEY_VAR}" in serialized
    assert "sk-" not in serialized


def test_a_case_set_cannot_talk_pi_into_trusting_it():
    """A case set is untrusted input and its files land in the workspace pi runs in. Left at
    pi's default of `ask`, a non-interactive run resolves project trust from whatever the
    operator's global setting happens to be."""
    assert build_settings_json(tools=("read",))["defaultProjectTrust"] == "never"


def test_the_tool_set_is_pinned_rather_than_left_to_the_installed_version():
    settings = build_settings_json(tools=("read", "bash", "edit"))
    assert settings["defaultTools"] == ["read", "bash", "edit"]


def test_the_operators_own_pi_settings_are_kept_out(tmp_path):
    env = child_environment(tmp_path / "pi-agent", api_key="k")
    assert env["PI_CODING_AGENT_DIR"] == str(tmp_path / "pi-agent")
    assert env[API_KEY_VAR] == "k"
    # No update check, no package refresh, no telemetry: an update landing mid-sweep would
    # change the instrument between one row and the next.
    assert env["PI_OFFLINE"] == "1"


def test_an_inherited_pi_variable_cannot_redirect_the_config(tmp_path, monkeypatch):
    monkeypatch.setenv("PI_CODING_AGENT_DIR", "/Users/someone/.pi/agent")
    monkeypatch.setenv("PI_TELEMETRY", "1")
    env = child_environment(tmp_path / "pi-agent", api_key="k")
    assert env["PI_CODING_AGENT_DIR"] == str(tmp_path / "pi-agent")
    assert env["PI_TELEMETRY"] == "0"


def test_the_harnesss_own_directory_does_not_travel_with_the_child(tmp_path, monkeypatch):
    monkeypatch.setenv("PWD", "/Users/someone/repo")
    monkeypatch.setenv("OLDPWD", "/Users/someone")
    env = child_environment(tmp_path / "pi-agent", api_key="k")
    assert "PWD" not in env and "OLDPWD" not in env


# --- usage accounting -----------------------------------------------------------------------


def test_tokens_are_summed_across_assistant_messages_not_taken_from_the_last():
    stream = "\n".join(
        [
            _assistant(_usage(640, 43, cache_read=256)),
            _assistant(_usage(211, 85, cache_read=768)),
            _assistant(_usage(93, 71, cache_read=1280), text="done"),
        ]
    )
    usage = parse_events(stream, Path("/ws"))["usage"]
    assert usage.model_calls == 3
    assert usage.total_tokens == (640 + 43 + 256) + (211 + 85 + 768) + (93 + 71 + 1280)
    assert usage.completion_tokens == 43 + 85 + 71


def test_the_same_usage_reported_on_four_events_is_counted_once():
    """pi puts a *cumulative* usage on `message_update`, re-emits the message on `turn_end`,
    and re-emits every message again on `agent_end`. A parser that adds any usage it finds
    reports three to four times the real cost and still passes `total_tokens > 0`."""
    message = {
        "role": "assistant",
        "stopReason": "stop",
        "content": [],
        "usage": _usage(100, 20),
    }
    stream = "\n".join(
        [
            json.dumps({"type": "message_update", "usage": _usage(100, 20)}),
            json.dumps({"type": "message_end", "message": message}),
            json.dumps({"type": "turn_end", "message": message, "toolResults": []}),
            json.dumps({"type": "agent_end", "messages": [message]}),
        ]
    )
    usage = parse_events(stream, Path("/ws"))["usage"]
    assert usage.model_calls == 1
    assert usage.total_tokens == 120


def test_reasoning_tokens_are_not_added_on_top_of_the_output_count():
    """pi takes `output` straight from `completion_tokens`, which already includes reasoning.
    The opencode adapter adds them, correctly, for its own schema; copied here it would roughly
    double the completion count for a reasoning model."""
    usage = parse_events(_assistant(_usage(10, 200, reasoning=180)), Path("/ws"))["usage"]
    assert usage.completion_tokens == 200


def test_the_prompt_and_completion_split_adds_up_to_the_total():
    usage = parse_events(_assistant(_usage(93, 71, cache_read=1280, cache_write=64)), Path("/ws"))[
        "usage"
    ]
    assert usage.prompt_tokens + usage.completion_tokens == usage.total_tokens


def test_a_user_or_tool_message_carries_no_model_call():
    stream = "\n".join(
        [
            json.dumps({"type": "message_end", "message": {"role": "user", "content": []}}),
            json.dumps(
                {
                    "type": "message_end",
                    "message": {"role": "toolResult", "usage": _usage(9, 9)},
                }
            ),
        ]
    )
    assert parse_events(stream, Path("/ws"))["usage"].model_calls == 0


def test_a_malformed_line_does_not_lose_the_run():
    stream = "not json\n" + _assistant(_usage(5, 5)) + "\n{oops\n"
    assert parse_events(stream, Path("/ws"))["usage"].model_calls == 1


# --- steps and the audit counters -----------------------------------------------------------


def test_turns_and_tool_calls_are_counted_separately():
    """One turn can issue several tool calls, so the step budget and the step list do not
    measure the same thing."""
    stream = "\n".join(
        [
            json.dumps({"type": "turn_start"}),
            json.dumps(
                {
                    "type": "tool_execution_start",
                    "toolCallId": "a",
                    "toolName": "read",
                    "args": {"path": "clamp.py"},
                }
            ),
            json.dumps(
                {
                    "type": "tool_execution_end",
                    "toolCallId": "a",
                    "toolName": "read",
                    "result": "ok",
                    "isError": False,
                }
            ),
            json.dumps(
                {
                    "type": "tool_execution_start",
                    "toolCallId": "b",
                    "toolName": "bash",
                    "args": {"command": "pytest -q"},
                }
            ),
            json.dumps(
                {
                    "type": "tool_execution_end",
                    "toolCallId": "b",
                    "toolName": "bash",
                    "result": "boom",
                    "isError": True,
                }
            ),
            json.dumps({"type": "turn_end"}),
        ]
    )
    parsed = parse_events(stream, Path("/ws"))
    assert parsed["turns"] == 1
    assert [s.tool for s in parsed["steps"]] == ["read", "bash"]
    assert parsed["tool_errors"] == 1
    assert parsed["steps"][0].detail.startswith("ok:")
    assert parsed["steps"][1].detail.startswith("error:")


@pytest.mark.parametrize(
    "args",
    [
        {"path": "../../secret"},
        {"path": "~/secret"},
        {"command": "cat /etc/passwd"},
        {"glob": "/Users/someone/**/answer.json"},
    ],
)
def test_a_path_outside_the_workspace_is_recorded(args, tmp_path):
    """Counted whether or not pi refused it: the point is to tell 'did not happen' apart from
    'was not measured'."""
    tool = "bash" if "command" in args else "read"
    assert escapes_workspace(tool, args, tmp_path) is True


def test_escape_attempts_reach_the_artifacts(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    stream = json.dumps(
        {
            "type": "tool_execution_start",
            "toolCallId": "a",
            "toolName": "read",
            "args": {"path": "../../benchmarks/ai_coding/cases/_clean2026/rev-abc.json"},
        }
    )
    assert parse_events(stream, ws)["out_of_workspace_attempts"]


def test_a_failed_provider_call_is_recorded_as_a_session_error():
    """pi emits a turn even when the provider call failed, and the stop reason is the only
    place that says so. Without it the run reads as an ordinary short answer."""
    stream = _assistant(_usage(0, 0), stop="error")
    assert parse_events(stream, Path("/ws"))["session_errors"]


# --- driving the binary ---------------------------------------------------------------------

_FAKE_EDITS = """
import json, sys
print(json.dumps({"type": "turn_end"}))
print(json.dumps({"type": "message_end", "message": {
    "role": "assistant", "stopReason": "stop", "content": [{"type": "text", "text": "fixed"}],
    "usage": {"input": 10, "output": 5, "cacheRead": 0, "cacheWrite": 0, "totalTokens": 15}}}))
open("clamp.py", "w").write("def clamp(v, lo, hi):\\n    return max(lo, min(v, hi))\\n")
"""


def test_an_edit_in_the_staging_copy_reaches_the_graded_workspace(tmp_path, monkeypatch):
    ws = _workspace(tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    agent = _agent(binary=_fake_cli(tmp_path, _FAKE_EDITS))
    result = agent.run(CASE, ws, max_steps=5, max_wall_time_s=60)
    assert result.status == "completed"
    assert "max(lo," in (ws / "clamp.py").read_text()
    assert result.artifacts["files_written"] == ["clamp.py"]
    assert result.empty_patch is False
    assert result.usage.total_tokens == 15


def test_a_deletion_mirrors_back_rather_than_merging(tmp_path, monkeypatch):
    ws = _workspace(tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    body = """
import json, os
print(json.dumps({"type": "message_end", "message": {"role": "assistant",
    "stopReason": "stop", "content": [], "usage": {"input": 1, "output": 1,
    "cacheRead": 0, "cacheWrite": 0, "totalTokens": 2}}}))
os.remove("clamp.py")
"""
    _agent(binary=_fake_cli(tmp_path, body)).run(CASE, ws, max_steps=5, max_wall_time_s=60)
    assert not (ws / "clamp.py").exists()


def test_a_run_that_changes_nothing_is_an_empty_patch(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    body = """
import json
print(json.dumps({"type": "message_end", "message": {"role": "assistant",
    "stopReason": "stop", "content": [], "usage": {"input": 1, "output": 1,
    "cacheRead": 0, "cacheWrite": 0, "totalTokens": 2}}}))
"""
    ws = _workspace(tmp_path)
    result = _agent(binary=_fake_cli(tmp_path, body)).run(CASE, ws, max_steps=5, max_wall_time_s=60)
    assert result.empty_patch is True


def test_the_step_budget_stops_a_run_that_would_not_stop_itself(tmp_path, monkeypatch):
    """pi has no step flag, so this is the adapter's own kill. If it does not fire, the fake
    runs for a minute and the test fails on the wall clock instead."""
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    body = """
import json, sys, time
for i in range(1000):
    print(json.dumps({"type": "message_end", "message": {"role": "assistant",
        "stopReason": "toolUse", "content": [], "usage": {"input": 1, "output": 1,
        "cacheRead": 0, "cacheWrite": 0, "totalTokens": 2}}}), flush=True)
    print(json.dumps({"type": "turn_end"}), flush=True)
    time.sleep(0.05)
"""
    ws = _workspace(tmp_path)
    result = _agent(binary=_fake_cli(tmp_path, body)).run(CASE, ws, max_steps=3, max_wall_time_s=60)
    assert result.status == "timeout"
    assert result.error_message == "max_steps exceeded"
    assert result.artifacts["turns"] == 3


def test_a_pinned_step_budget_overrides_the_run_config(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    body = """
import json, time
for i in range(1000):
    print(json.dumps({"type": "turn_end"}), flush=True)
    time.sleep(0.05)
"""
    ws = _workspace(tmp_path)
    agent = _agent(max_steps=2, binary=_fake_cli(tmp_path, body))
    result = agent.run(CASE, ws, max_steps=40, max_wall_time_s=60)
    assert result.artifacts["turns"] == 2


def test_a_chatty_stderr_does_not_deadlock_the_run(tmp_path, monkeypatch):
    """Streaming stdout while stderr is an undrained pipe blocks the child as soon as that
    buffer fills -- the run then dies on the wall clock and files as a timeout, which is a
    wrong number reached through an entirely green code path."""
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    body = """
import json, sys
sys.stderr.write("x" * 500000)
sys.stderr.flush()
print(json.dumps({"type": "message_end", "message": {"role": "assistant",
    "stopReason": "stop", "content": [], "usage": {"input": 1, "output": 1,
    "cacheRead": 0, "cacheWrite": 0, "totalTokens": 2}}}))
"""
    ws = _workspace(tmp_path)
    result = _agent(binary=_fake_cli(tmp_path, body)).run(CASE, ws, max_steps=5, max_wall_time_s=30)
    assert result.status == "completed"


def test_a_hang_with_no_model_call_is_infrastructure_not_a_timeout(tmp_path, monkeypatch):
    """Measured: pointed at a dead gateway pi emits nothing and does not exit. Calling that a
    timeout charges an outage to the model, keeps it in the denominator, and skips the retry."""
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    body = "import time\ntime.sleep(30)\n"
    ws = _workspace(tmp_path)
    result = _agent(binary=_fake_cli(tmp_path, body)).run(CASE, ws, max_steps=5, max_wall_time_s=2)
    assert result.status == "infra_error"


def test_a_timeout_after_the_model_answered_is_a_timeout(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    body = """
import json, time
print(json.dumps({"type": "message_end", "message": {"role": "assistant",
    "stopReason": "toolUse", "content": [], "usage": {"input": 1, "output": 1,
    "cacheRead": 0, "cacheWrite": 0, "totalTokens": 2}}}), flush=True)
time.sleep(30)
"""
    ws = _workspace(tmp_path)
    result = _agent(binary=_fake_cli(tmp_path, body)).run(CASE, ws, max_steps=5, max_wall_time_s=2)
    assert result.status == "timeout"
    assert result.error_message == "max_wall_time exceeded"


def test_a_crash_before_any_model_call_is_infrastructure_not_failure(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    body = "import sys\nsys.exit(3)\n"
    ws = _workspace(tmp_path)
    result = _agent(binary=_fake_cli(tmp_path, body)).run(CASE, ws, max_steps=5, max_wall_time_s=60)
    assert result.status == "infra_error"


def test_a_missing_binary_is_infrastructure(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    ws = _workspace(tmp_path)
    result = _agent(binary="pi-that-is-not-installed").run(
        CASE, ws, max_steps=5, max_wall_time_s=60
    )
    assert result.status == "infra_error"
    assert "not found" in (result.error_message or "")


def test_a_missing_api_key_is_infrastructure(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("AIBENCH_API_KEY", raising=False)
    ws = _workspace(tmp_path)
    result = _agent(binary=_fake_cli(tmp_path, "pass\n")).run(
        CASE, ws, max_steps=5, max_wall_time_s=60
    )
    assert result.status == "infra_error"
    assert "API key" in (result.error_message or "")


def test_a_scaffold_version_other_than_the_pinned_one_is_refused(tmp_path, monkeypatch):
    """A different scaffold version is a different instrument, and pi ships roughly weekly."""
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    body = 'import sys\nprint("9.9.9")\n'
    ws = _workspace(tmp_path)
    agent = _agent(binary=_fake_cli(tmp_path, body), expected_version="0.84.3")
    result = agent.run(CASE, ws, max_steps=5, max_wall_time_s=60)
    assert result.status == "infra_error"
    assert "0.84.3" in (result.error_message or "")


def test_measuring_without_a_filesystem_boundary_is_refused(tmp_path, monkeypatch):
    """pi documents that it ships no sandbox, so there is nothing to fall back to."""
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    monkeypatch.delenv("AIBENCH_ALLOW_UNSANDBOXED", raising=False)
    monkeypatch.setattr("aibench.agents.pi.SANDBOX_EXEC", tmp_path / "no-sandbox-exec-here")
    ws = _workspace(tmp_path)
    agent = _agent(sandbox=True, binary=_fake_cli(tmp_path, "pass\n"))
    result = agent.run(CASE, ws, max_steps=5, max_wall_time_s=60)
    assert result.status == "infra_error"
    assert "boundary" in (result.error_message or "")


def test_the_registry_builds_it(tmp_path):
    ac = AgentConfig.from_dict({"name": "pi", "version": "1", "adapter": "pi", "options": {}})
    mc = ModelConfig.from_dict({"name": "m", "provider": "openai_compat", "model": "GLM-5.2"})
    assert isinstance(create_agent(ac, mc), PiAgent)


# --- what the review found ---------------------------------------------------------------


def test_the_boundary_covers_the_git_object_store_not_just_the_module_tree():
    """`repo_root()` is `benchmarks/coding`; the `.git` that serves its contents is a level
    above it. A profile denying only `repo_root()` refused `cat` on a case JSON and handed the
    same bytes to `git -C <monorepo> show HEAD:<path>` -- the gold solution and hidden tests,
    read straight out of the object store."""
    from aibench.agents.cli_sandbox import checkout_above, protected_root, sandbox_profile
    from aibench.io_util import repo_root

    protected = protected_root()
    checkout = checkout_above(repo_root().resolve())
    if checkout is not None:
        assert protected == checkout.resolve()
        assert (protected / ".git").exists()
    profile = sandbox_profile(protected, readable=(repo_root() / ".venv",))
    assert f'(deny file-read-data file-write* (subpath "{protected}"))' in profile
    # And the interpreter the grader needs is still reachable.
    assert f'(allow file-read-data (subpath "{repo_root() / ".venv"}"))' in profile


def test_an_undecodable_byte_does_not_outlive_the_deadline(tmp_path, monkeypatch):
    """pi is Node; a lone surrogate reaches the pipe as invalid UTF-8. Strict decoding raised
    out of the read loop past the `finally` that only cancels the timer, so the deadline was
    gone and the child ran on -- measured at 61s against a 3s budget."""
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    body = """
import sys, time
sys.stdout.buffer.write(b'\\xff\\xfe not utf-8\\n')
sys.stdout.buffer.flush()
time.sleep(30)
"""
    ws = _workspace(tmp_path)
    result = _agent(binary=_fake_cli(tmp_path, body)).run(CASE, ws, max_steps=5, max_wall_time_s=3)
    assert result.status == "infra_error"
    assert result.wall_time_s < 20


def test_a_gateway_that_failed_every_turn_is_infrastructure_not_budget_exhaustion(
    tmp_path, monkeypatch
):
    """Otherwise the run burns its whole turn budget on failing calls, gets killed by this
    adapter, and files as `timeout` -- graded, left in the denominator, never retried."""
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    body = """
import json, time
for _ in range(1000):
    print(json.dumps({"type": "message_end", "message": {"role": "assistant",
        "stopReason": "error", "errorMessage": "502 from gateway", "content": [],
        "usage": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0,
                  "totalTokens": 0}}}), flush=True)
    print(json.dumps({"type": "turn_end"}), flush=True)
    time.sleep(0.02)
"""
    ws = _workspace(tmp_path)
    result = _agent(binary=_fake_cli(tmp_path, body)).run(CASE, ws, max_steps=3, max_wall_time_s=60)
    assert result.status == "infra_error"
    assert "session error" in (result.error_message or "")


def test_the_token_total_is_derived_rather_than_trusted():
    """`prompt + completion == total` is asserted in the manual, so it has to hold by
    construction. A gateway that omits `totalTokens` would otherwise zero the headline token
    count and the cost estimate built on it, with nothing to show for it."""
    no_total = json.dumps(
        {
            "type": "message_end",
            "message": {
                "role": "assistant",
                "stopReason": "stop",
                "content": [],
                "usage": {"input": 100, "output": 20, "cacheRead": 30, "cacheWrite": 0},
            },
        }
    )
    usage = parse_events(no_total, Path("/ws"))["usage"]
    assert usage.total_tokens == 150
    assert usage.prompt_tokens + usage.completion_tokens == usage.total_tokens
