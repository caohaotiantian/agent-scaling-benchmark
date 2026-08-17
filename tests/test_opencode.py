"""The adapter that measures a model inside a coding agent people actually use.

The two properties worth locking down are the ones that were measured rather than assumed:
the workspace boundary only holds when the staging directory is outside a git checkout, and
per-step token counts have to be summed -- the last event alone reports one turn as the cost
of the whole run.
"""

from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest

from aibench.agents.opencode import (
    AGENT_ID,
    PROVIDER_ID,
    OpenCodeAgent,
    _checkout_above,
    _escapes_workspace,
    build_config,
    changed_paths,
    child_environment,
    parse_events,
    sandbox_profile,
    snapshot,
    workspace_permissions,
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


def _agent(**opts) -> OpenCodeAgent:
    # These tests drive a stand-in binary, not a measurement. The production default is
    # sandbox=True, which refuses on Linux because sandbox-exec is macOS-only. Tests that
    # want that refusal pass sandbox=True themselves.
    opts.setdefault("sandbox", False)
    ac = AgentConfig.from_dict(
        {"name": "oc", "version": "1", "adapter": "opencode", "options": opts}
    )
    mc = ModelConfig.from_dict(
        {
            "name": "m",
            "provider": "openai_compat",
            "model": "GLM-5.1",
            "base_url": "http://gw.test/v1",
            "api_key_env": "OPENAI_API_KEY",
        }
    )
    return OpenCodeAgent(ac, mc)


def _workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "workspace"
    ws.mkdir()
    (ws / "clamp.py").write_text("def clamp(v, lo, hi):\n    return min(v, hi)\n")
    (ws / "test_clamp.py").write_text("def test_low():\n    assert True\n")
    return ws


def _fake_cli(tmp_path: Path, body: str) -> str:
    """An executable standing in for opencode, so the adapter is testable without a gateway."""
    script = tmp_path / "fake-opencode"
    script.write_text("#!/usr/bin/env python3\n" + body)
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return str(script)


# --- configuration -------------------------------------------------------------------------


def test_the_gateway_comes_from_the_model_config_not_the_operators_machine(tmp_path):
    """A measurement that reads ~/.config/opencode cannot be recomputed from this repository."""
    cfg = build_config(
        model=ModelConfig.from_dict({"name": "m", "provider": "openai_compat", "model": "GLM-5.1"}),
        api_key="secret",
        base_url="http://gw.test/v1",
        model_name="GLM-5.1",
        max_steps=7,
        system_prompt="fix it",
        allowed_commands=("ls",),
    )
    provider = cfg["provider"][PROVIDER_ID]
    assert provider["options"]["baseURL"] == "http://gw.test/v1"
    assert provider["options"]["apiKey"] == "secret"
    assert list(provider["models"]) == ["GLM-5.1"]
    assert cfg["agent"][AGENT_ID]["maxSteps"] == 7
    assert cfg["autoupdate"] is False
    assert cfg["instructions"] == []


def test_network_tools_and_subagents_are_off():
    """Web access would make the run depend on the open internet; subagents would make the
    step budget mean something other than steps."""
    cfg = build_config(
        model=ModelConfig.from_dict({"name": "m", "provider": "openai_compat", "model": "M"}),
        api_key="k",
        base_url="http://gw/v1",
        model_name="M",
        max_steps=3,
        system_prompt="p",
        allowed_commands=("ls",),
    )
    tools = cfg["agent"][AGENT_ID]["tools"]
    assert tools["webfetch"] is False
    assert tools["websearch"] is False
    assert tools["task"] is False
    assert tools["skill"] is False


def test_bookkeeping_tools_do_not_spend_the_step_budget():
    """A measured run spent three of sixteen steps on todowrite. The step budget is the one
    variable the anchor ladder moves, so a one-step rung could spend it on a to-do list."""
    cfg = build_config(
        model=ModelConfig.from_dict({"name": "m", "provider": "openai_compat", "model": "M"}),
        api_key="k",
        base_url="http://gw/v1",
        model_name="M",
        max_steps=1,
        system_prompt="p",
        allowed_commands=("ls",),
    )
    assert cfg["agent"][AGENT_ID]["tools"]["todowrite"] is False


def test_external_directory_is_denied_explicitly():
    """It defaults to `ask`, and --auto approves anything not explicitly denied, so omitting
    the rule is the same as allowing the agent out of the workspace. It is also the *whole*
    filesystem boundary -- measured denying an outside path through read and through bash."""
    perms = workspace_permissions(allowed=("ls",))
    assert perms["external_directory"] == "deny"


def test_no_path_rules_lock_the_agent_out_of_its_own_workspace():
    """`bash` rules match the raw command string, so absolute patterns work there. `read` and
    `edit` do not match an absolute pattern, so a {"*": "deny"} fallback plus allow-rules that
    never match refuses every read and every edit inside the workspace -- measured as the agent
    unable to fix a two-line defect, with nothing in the pass rate to show for it."""
    perms = workspace_permissions(allowed=("ls",))
    assert "read" not in perms
    assert "edit" not in perms


def test_the_bash_allowlist_is_ordered_broad_then_specific():
    """opencode applies the last matching rule, so the blanket deny must come first and the
    refusals last -- reversing it would allow everything."""
    perms = workspace_permissions(allowed=("python", "ls"))
    keys = list(perms["bash"])
    assert keys[0] == "*"
    assert perms["bash"]["python *"] == "allow"
    assert perms["bash"]["rm *"] == "deny"
    assert keys.index("python *") < keys.index("rm *")


def test_a_pinned_step_budget_overrides_the_run_config(tmp_path, monkeypatch):
    """A ladder rung has to stay at its rung whichever run yaml drives it."""
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    agent = _agent(max_steps=3, binary=_fake_cli(tmp_path, "raise SystemExit(0)\n"))
    assert agent.max_steps_cap == 3


# --- event parsing -------------------------------------------------------------------------


def test_tokens_are_summed_across_steps_not_taken_from_the_last(tmp_path):
    """Each step_finish reports that step's own context. Reading only the final event would
    report a single turn as the cost of the run, which is the number the cost axis divides by.
    """
    stream = "\n".join(
        json.dumps(
            {
                "type": "step_finish",
                "part": {
                    "reason": "tool-calls",
                    "tokens": {
                        "total": total,
                        "input": inp,
                        "output": out,
                        "reasoning": 0,
                        "cache": {"read": cache, "write": 0},
                    },
                },
            }
        )
        for total, inp, out, cache in [(7597, 7515, 82, 0), (7847, 228, 67, 7552)]
    )
    parsed = parse_events(stream, tmp_path)
    usage = parsed["usage"]
    assert usage.model_calls == 2
    assert usage.total_tokens == 7597 + 7847
    assert usage.prompt_tokens == 7515 + 228 + 7552
    assert usage.completion_tokens == 82 + 67


def test_a_malformed_line_does_not_lose_the_run(tmp_path):
    stream = "not json\n" + json.dumps(
        {"type": "step_finish", "part": {"tokens": {"total": 10, "input": 8, "output": 2}}}
    )
    assert parse_events(stream, tmp_path)["usage"].total_tokens == 10


def test_the_step_limit_is_reported_as_a_timeout(tmp_path):
    stream = json.dumps({"type": "text", "part": {"text": "Maximum steps for this agent"}})
    assert parse_events(stream, tmp_path)["hit_step_limit"] is True


def test_refused_tool_calls_are_counted(tmp_path):
    stream = json.dumps(
        {
            "type": "tool_use",
            "part": {
                "tool": "bash",
                "state": {"status": "error", "input": {"command": "rm -rf ."}},
            },
        }
    )
    assert parse_events(stream, tmp_path)["tool_errors"] == 1


# --- the workspace boundary ----------------------------------------------------------------


def test_an_absolute_path_outside_the_workspace_is_recorded(tmp_path):
    """Counted independently of opencode's refusal wording: this records what was attempted,
    so a future version that stops denying cannot read as clean."""
    ws = tmp_path / "ws"
    assert _escapes_workspace("read", {"filePath": "/etc/passwd"}, ws)
    assert _escapes_workspace("bash", {"command": "cat /etc/passwd"}, ws)
    assert not _escapes_workspace("read", {"filePath": f"{ws}/clamp.py"}, ws)
    assert not _escapes_workspace("bash", {"command": "python -m pytest -q"}, ws)


def test_traversal_and_home_shorthand_count_as_escapes(tmp_path):
    """A prefix test on the raw string reported all three of these as clean."""
    ws = tmp_path / "ws"
    assert _escapes_workspace("bash", {"command": "cat ../../opencode.json"}, ws)
    assert _escapes_workspace("read", {"filePath": "~/.aws/credentials"}, ws)
    assert _escapes_workspace("bash", {"command": "grep -r secret ../"}, ws)


def test_a_sibling_directory_sharing_the_prefix_is_not_inside(tmp_path):
    ws = tmp_path / "ws"
    assert _escapes_workspace("read", {"filePath": f"{ws}-other/secret.txt"}, ws)


def test_escape_attempts_reach_the_artifacts(tmp_path):
    stream = json.dumps(
        {
            "type": "tool_use",
            "part": {
                "tool": "read",
                "state": {"status": "error", "input": {"filePath": "/repo/gold.json"}},
            },
        }
    )
    parsed = parse_events(stream, tmp_path / "ws")
    assert len(parsed["out_of_workspace_attempts"]) == 1
    assert "gold.json" in parsed["out_of_workspace_attempts"][0]


def test_a_staging_dir_inside_a_checkout_is_detected(tmp_path):
    """opencode resolves its project root by walking up to the git root, so a staging
    directory inside a checkout puts the whole repository -- including the case's gold
    solution -- inside 'the project', where external_directory matches nothing."""
    (tmp_path / ".git").mkdir()
    nested = tmp_path / "runs" / "cases" / "c1"
    nested.mkdir(parents=True)
    assert _checkout_above(nested) == tmp_path


def test_a_staging_dir_outside_any_checkout_is_clean(tmp_path):
    assert _checkout_above(tmp_path) is None


# --- the workspace snapshot ----------------------------------------------------------------


def test_caches_are_not_counted_as_edits(tmp_path):
    """Running pytest creates __pycache__. Counting it would make empty_patch false for a run
    that changed nothing."""
    ws = _workspace(tmp_path)
    before = snapshot(ws)
    (ws / "__pycache__").mkdir()
    (ws / "__pycache__" / "clamp.cpython-314.pyc").write_bytes(b"\x00")
    assert changed_paths(before, snapshot(ws)) == []


def test_a_deletion_counts_as_a_change(tmp_path):
    ws = _workspace(tmp_path)
    before = snapshot(ws)
    (ws / "clamp.py").unlink()
    assert changed_paths(before, snapshot(ws)) == ["clamp.py"]


# --- end to end against a stand-in binary --------------------------------------------------

_FAKE_EDITS = """
import json, sys
args = sys.argv[1:]
d = args[args.index("--dir") + 1]
open(d + "/clamp.py", "w").write("def clamp(v, lo, hi):\\n    return max(lo, min(v, hi))\\n")
print(json.dumps({"type": "tool_use", "part": {"tool": "edit",
    "state": {"status": "completed", "input": {"filePath": d + "/clamp.py"}}}}))
print(json.dumps({"type": "step_finish", "part": {"reason": "stop",
    "tokens": {"total": 120, "input": 100, "output": 20, "reasoning": 0,
               "cache": {"read": 0, "write": 0}}}}))
print(json.dumps({"type": "text", "part": {"text": "done"}}))
"""


def test_an_edit_in_the_staging_copy_reaches_the_graded_workspace(tmp_path, monkeypatch):
    """The agent works on a mirror outside the repository; the grader was handed the original
    path before the run, so the mirror has to come back to it."""
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    ws = _workspace(tmp_path)
    agent = _agent(binary=_fake_cli(tmp_path, _FAKE_EDITS))
    result = agent.run(CASE, ws, max_steps=5, max_wall_time_s=60)

    assert result.status == "completed"
    assert "max(lo, min(v, hi))" in (ws / "clamp.py").read_text()
    assert result.empty_patch is False
    assert result.artifacts["files_written"] == ["clamp.py"]
    assert result.usage.total_tokens == 120
    assert result.usage.model_calls == 1


_FAKE_DELETES = """
import json, os, sys
args = sys.argv[1:]
d = args[args.index("--dir") + 1]
os.remove(d + "/clamp.py")
print(json.dumps({"type": "step_finish", "part": {"tokens": {"total": 5, "input": 4, "output": 1}}}))
"""


def test_a_deletion_mirrors_back_rather_than_merging(tmp_path, monkeypatch):
    """Copying the mirror over the original would leave a deleted file in place and grade a
    workspace the agent never produced."""
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    ws = _workspace(tmp_path)
    agent = _agent(binary=_fake_cli(tmp_path, _FAKE_DELETES))
    agent.run(CASE, ws, max_steps=5, max_wall_time_s=60)
    assert not (ws / "clamp.py").exists()
    assert (ws / "test_clamp.py").exists()


def test_a_run_that_changes_nothing_is_an_empty_patch(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    ws = _workspace(tmp_path)
    body = (
        'import json\nprint(json.dumps({"type": "step_finish", "part": '
        '{"tokens": {"total": 3, "input": 2, "output": 1}}}))\n'
    )
    result = _agent(binary=_fake_cli(tmp_path, body)).run(CASE, ws, max_steps=5, max_wall_time_s=60)
    assert result.empty_patch is True
    assert result.artifacts["files_written"] == []


def test_a_crash_before_any_model_call_is_infrastructure_not_failure(tmp_path, monkeypatch):
    """A launch or configuration failure scored as a failed attempt reads as model weakness --
    the misreading that made an all-infra_error experiment report 0.0% as a capability result.
    """
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    ws = _workspace(tmp_path)
    body = 'import sys\nsys.stderr.write("boom\\n")\nraise SystemExit(2)\n'
    result = _agent(binary=_fake_cli(tmp_path, body)).run(CASE, ws, max_steps=5, max_wall_time_s=60)
    assert result.status == "infra_error"


_FAKE_GATEWAY_DIES = """
import json, sys
print(json.dumps({"type": "step_finish", "part": {"tokens": {"total": 90, "input": 80, "output": 10}}}))
print(json.dumps({"type": "error", "error": {"name": "ProviderAuthError", "message": "401"}}))
raise SystemExit(1)
"""


def test_a_gateway_failure_after_the_first_step_is_infrastructure(tmp_path, monkeypatch):
    """opencode reports the outage as an `error` event and exits non-zero. Keying the infra
    branch on "no model calls" alone sent this to `failed`: the row stayed in the success-rate
    denominator, was never retried, and a 429 was charged to the model as weakness."""
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    result = _agent(binary=_fake_cli(tmp_path, _FAKE_GATEWAY_DIES)).run(
        CASE, _workspace(tmp_path), max_steps=5, max_wall_time_s=60
    )
    assert result.status == "infra_error"
    assert "ProviderAuthError" in (result.error_message or "")
    assert result.usage.total_tokens == 90


_FAKE_HANGS = """
import time
time.sleep(30)
"""


def test_a_hang_with_no_model_call_is_infrastructure_not_a_timeout(tmp_path, monkeypatch):
    """A dead gateway or a wrong base_url hangs exactly like this. Reporting it as a timeout
    would put an outage in the results as budget exhaustion -- graded, counted, not retried."""
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    result = _agent(binary=_fake_cli(tmp_path, _FAKE_HANGS)).run(
        CASE, _workspace(tmp_path), max_steps=5, max_wall_time_s=2
    )
    assert result.status == "infra_error"
    assert result.usage.model_calls == 0


def test_the_operators_own_opencode_settings_are_kept_out(tmp_path):
    """OPENCODE_CONFIG adds a config, it does not replace the global one, so the operator's
    ~/.config/opencode was still merged in. OPENCODE_PERMISSION left in a shell would override
    the permission block wholesale."""
    env = child_environment(tmp_path, tmp_path / "opencode.json")
    assert env["XDG_CONFIG_HOME"] == str(tmp_path / "config")
    assert env["XDG_DATA_HOME"] == str(tmp_path / "data")
    assert "OPENCODE_PERMISSION" not in env
    assert env["OPENCODE_CONFIG"] == str(tmp_path / "opencode.json")


def test_the_harnesss_own_directory_does_not_travel_with_the_child(tmp_path, monkeypatch):
    """PWD still named this repository even after the child was given another cwd, and opencode
    stats what PWD points at during startup -- under a sandbox that hides the repo it exited
    with EPERM before reaching the gateway."""
    monkeypatch.setenv("PWD", "/Users/someone/repo")
    monkeypatch.setenv("OLDPWD", "/Users/someone")
    env = child_environment(tmp_path, tmp_path / "opencode.json")
    assert "PWD" not in env
    assert "OLDPWD" not in env


def test_an_inherited_opencode_variable_cannot_reopen_the_permissions(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENCODE_PERMISSION", '{"external_directory":"allow"}')
    monkeypatch.setenv("OPENCODE_MODEL", "someone/else")
    env = child_environment(tmp_path, tmp_path / "opencode.json")
    assert "OPENCODE_PERMISSION" not in env
    assert "OPENCODE_MODEL" not in env


def test_the_sandbox_profile_hides_the_repo_but_keeps_the_interpreter(tmp_path):
    """The threat is the agent reading the case's own gold solution out of this repository.
    The virtualenv has to stay readable or the agent cannot run the tests it is graded on."""
    repo = tmp_path / "repo"
    profile = sandbox_profile(repo, readable=(repo / ".venv",))
    assert f'(deny file-read-data file-write* (subpath "{repo}"))' in profile
    assert f'(allow file-read-data (subpath "{repo / ".venv"}"))' in profile
    # Metadata stays readable: denying it too made opencode exit with EPERM on lstat before it
    # ever reached the gateway, and the gold solution is bytes inside a file, not a stat.
    assert "file-read-metadata" not in profile


def test_a_platform_without_a_sandbox_is_refused_unless_asked(tmp_path, monkeypatch):
    """Linux has no sandbox-exec. Measuring anyway used to be the default, so a replay there
    was a different instrument. The opt-out is `options.sandbox: false` or
    AIBENCH_ALLOW_UNSANDBOXED=1 — this helper uses the former; this test checks the refuse."""
    import aibench.agents.opencode as adapter

    monkeypatch.setenv("OPENAI_API_KEY", "k")
    monkeypatch.setattr(adapter, "_SANDBOX_EXEC", tmp_path / "absent")
    result = _agent(binary=_fake_cli(tmp_path, "raise SystemExit(0)\n"), sandbox=True).run(
        CASE, _workspace(tmp_path), max_steps=5, max_wall_time_s=60
    )
    assert result.status == "infra_error"
    assert "sandbox-exec is macOS-only" in (result.error_message or "")


def test_a_run_records_whether_it_was_actually_confined(tmp_path, monkeypatch):
    """A platform without sandbox-exec must not inherit a guarantee it never got."""
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    body = 'import json\nprint(json.dumps({"type": "text", "part": {"text": "hi"}}))\n'
    result = _agent(binary=_fake_cli(tmp_path, body), sandbox=False).run(
        CASE, _workspace(tmp_path), max_steps=5, max_wall_time_s=60
    )
    assert result.artifacts["sandboxed"] is False


def test_a_symlinked_directory_does_not_destroy_the_graded_workspace(tmp_path, monkeypatch):
    """The mirror empties the target before copying. Following a symlink-to-directory raised
    IsADirectoryError at that point, leaving the graded workspace wiped."""
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    ws = _workspace(tmp_path)
    (ws / "pkg").mkdir()
    (ws / "pkg" / "mod.py").write_text("x = 1\n")
    (ws / "link").symlink_to("pkg")
    body = 'import json\nprint(json.dumps({"type": "text", "part": {"text": "done"}}))\n'
    result = _agent(binary=_fake_cli(tmp_path, body), sandbox=False).run(
        CASE, ws, max_steps=5, max_wall_time_s=60
    )
    assert result.status == "completed"
    assert (ws / "clamp.py").exists()
    assert (ws / "link").is_symlink()


def test_a_missing_binary_is_infrastructure(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    result = _agent(binary=str(tmp_path / "nope")).run(
        CASE, _workspace(tmp_path), max_steps=5, max_wall_time_s=60
    )
    assert result.status == "infra_error"
    assert "nope" in (result.error_message or "")


_FAKE_REPORTS_ENV = """
import json, os, sys
args = sys.argv[1:]
d = args[args.index("--dir") + 1]
open(d + "/env.json", "w").write(json.dumps({
    "XDG_DATA_HOME": os.environ.get("XDG_DATA_HOME", ""),
    "project_config": os.environ.get("OPENCODE_DISABLE_PROJECT_CONFIG", ""),
}))
print(json.dumps({"type": "step_finish", "part": {"tokens": {"total": 1, "input": 1, "output": 0}}}))
"""


def test_each_run_gets_its_own_opencode_data_directory(tmp_path, monkeypatch):
    """opencode keeps sessions in one SQLite database under the user's data directory. Sharing
    it across concurrent matrix rows produced "database is locked", which costs the whole row
    as an infra_error rather than a result."""
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    ws = _workspace(tmp_path)
    _agent(binary=_fake_cli(tmp_path, _FAKE_REPORTS_ENV)).run(
        CASE, ws, max_steps=5, max_wall_time_s=60
    )
    seen = json.loads((ws / "env.json").read_text())
    assert seen["XDG_DATA_HOME"].endswith("/data")
    assert "aibench-opencode-" in seen["XDG_DATA_HOME"]
    assert seen["project_config"] == "1"


def test_a_missing_credential_never_reaches_the_gateway(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("AIBENCH_API_KEY", raising=False)
    result = _agent().run(CASE, _workspace(tmp_path), max_steps=5, max_wall_time_s=60)
    assert result.status == "infra_error"
    assert "API key" in (result.error_message or "")


def test_the_adapter_is_registered():
    mc = ModelConfig.from_dict({"name": "m", "provider": "openai_compat", "model": "M"})
    ac = AgentConfig.from_dict({"name": "oc", "version": "1", "adapter": "opencode"})
    assert isinstance(create_agent(ac, mc), OpenCodeAgent)


@pytest.mark.parametrize("rung,steps", [("opencode-s1", 1), ("opencode-s40", 40)])
def test_the_ladder_configs_pin_their_step_budget(rung, steps):
    """The panel this replaces inverted because its members differed in more than one way."""
    import yaml

    root = Path(__file__).resolve().parents[1]
    cfg = yaml.safe_load((root / "configs" / "agents" / f"{rung}.yaml").read_text())
    assert cfg["adapter"] == "opencode"
    assert cfg["options"]["max_steps"] == steps


def test_the_prompt_does_not_hand_over_the_files():
    """Pasting the workspace into the prompt is what made the single-turn anchor measure
    stronger than the multi-step one: with the tests in the prompt there is nothing to find."""
    message = _agent()._task_message(CASE)
    assert CASE.prompt in message
    assert "def clamp" not in message
    assert "def test_low" not in message
