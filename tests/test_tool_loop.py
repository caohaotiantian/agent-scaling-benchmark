"""The tool_loop agent's tool handlers, exercised without any LLM."""

from pathlib import Path

from aibench.agents.tool_loop import ToolLoopAgent
from aibench.models import AgentConfig, ModelConfig


def _agent(**options) -> ToolLoopAgent:
    return ToolLoopAgent(
        AgentConfig.from_dict(
            {"name": "tl", "adapter": "tool_loop", "version": "1", "options": options}
        ),
        ModelConfig.from_dict({"name": "m", "model": "m", "provider": "openai_compat"}),
    )


def _run(tool: str, data: dict, ws: Path, *, allow_bash: bool = True) -> str:
    return _agent()._run_tool(tool, data, ws, allow_bash=allow_bash, written=[])


def test_bash_output_shorter_than_the_truncation_window(tmp_path: Path):
    """Regression: `[-2000]` indexed one character instead of slicing, so every short
    command — i.e. essentially every command — raised IndexError and the whole case was
    recorded as infra_error rather than as an agent result."""
    obs = _run("bash", {"command": "echo hi"}, tmp_path)
    assert "exit=0" in obs
    assert "hi" in obs


def test_bash_empty_output_is_fine(tmp_path: Path):
    obs = _run("bash", {"command": "true"}, tmp_path)
    assert obs.startswith("exit=0")


def test_bash_failure_keeps_stderr(tmp_path: Path):
    obs = _run("bash", {"command": "ls /definitely/not/here"}, tmp_path)
    assert "exit=" in obs
    assert "exit=0" not in obs


def test_bash_output_longer_than_the_window_is_truncated(tmp_path: Path):
    obs = _run("bash", {"command": "python3 -c \"print('x' * 5000)\""}, tmp_path)
    assert "exit=0" in obs
    assert 1000 < len(obs) < 4000


def test_bash_can_be_disabled(tmp_path: Path):
    assert (
        _run("bash", {"command": "echo hi"}, tmp_path, allow_bash=False) == "error: bash disabled"
    )


def test_bash_rejects_shell_metacharacters(tmp_path: Path):
    for cmd in ("echo a; rm -rf /", "echo a && echo b", "echo a | tee f", "echo a > f"):
        assert _run("bash", {"command": cmd}, tmp_path) == "error: command not allowed"


def test_read_write_list_round_trip(tmp_path: Path):
    assert _run("write", {"path": "pkg/a.py", "content": "x = 1\n"}, tmp_path).startswith("wrote")
    assert _run("read", {"path": "pkg/a.py"}, tmp_path) == "x = 1\n"
    assert "pkg" in _run("list", {"path": "."}, tmp_path)


def test_path_escape_is_refused(tmp_path: Path):
    for tool in ("read", "write", "list"):
        assert _run(tool, {"path": "../../etc/passwd"}, tmp_path) == "error: path escape"


def test_unknown_tool_is_reported(tmp_path: Path):
    assert _run("teleport", {}, tmp_path).startswith("error: unknown tool")
