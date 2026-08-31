"""Minimal multi-step tool-loop agent: read/list/write + bash (sandbox cwd)."""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any

import httpx

from aibench.agents.base import AgentAdapter, request_timeout_s
from aibench.models import AgentRunResult, Case, StepRecord, UsageRecord


def _loads_tolerant(cleaned: str) -> Any:
    """Parse what a model really sends: fenced, prose-wrapped, or with real newlines inside."""
    try:
        return json.loads(cleaned, strict=False)
    except json.JSONDecodeError:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start < 0 or end <= start:
            raise
        return json.loads(cleaned[start : end + 1], strict=False)


def _strip_fences(text: str) -> str:
    text = (text or "").strip()
    m = re.match(r"^```(?:json)?\s*([\s\S]*?)\s*```$", text)
    return m.group(1).strip() if m else text


#: Programs a coding agent needs to inspect a workspace and run its tests. Everything else is
#: refused: the command runs on the host with the harness's own privileges, so this is the only
#: thing standing between generated text and the developer's machine until the grader runs in a
#: container. Override per agent with `options.allowed_commands`.
DEFAULT_ALLOWED_COMMANDS = (
    "python",
    "python3",
    "pytest",
    "node",
    "ls",
    "cat",
    "head",
    "tail",
    "wc",
    "grep",
    "find",
    "pwd",
    "echo",
    "true",
)

#: Shell syntax that escapes a single-program command: chaining, redirection, substitution.
_SHELL_ESCAPES = (";", "&&", "||", "|", ">", "<", "`", "$(", "${", "&", "\n")


def check_bash_command(cmd: str, *, allowed: tuple[str, ...]) -> str | None:
    """Refusal message for a command the sandbox will not run, or None to allow it.

    An allowlist rather than a blocklist. The previous blocklist let `curl`, `rm -rf` and
    `$(...)` substitution through, which on an unsandboxed host means a benchmarked model can
    reach anything the harness can.
    """
    cmd = (cmd or "").strip()
    if not cmd:
        return "error: empty command"
    for token in _SHELL_ESCAPES:
        if token in cmd:
            return f"error: shell metacharacter not allowed: {token!r}"
    program = cmd.split()[0].rsplit("/", 1)[-1]
    if program not in allowed:
        return f"error: command not allowed: {program} (allowed: {', '.join(sorted(allowed))})"
    return None


class ToolLoopAgent(AgentAdapter):
    """Agent that may call tools in a loop until submit or max_steps."""

    @property
    def allowed_commands(self) -> tuple[str, ...]:
        configured = self.agent_config.options.get("allowed_commands")
        return tuple(configured) if configured else DEFAULT_ALLOWED_COMMANDS

    def run(
        self,
        case: Case,
        workspace: Path,
        *,
        max_steps: int,
        max_wall_time_s: float,
    ) -> AgentRunResult:
        t0 = time.perf_counter()
        model = self.model_config
        api_key_env = model.api_key_env or "OPENAI_API_KEY"
        api_key = os.environ.get(api_key_env) or os.environ.get("AIBENCH_API_KEY")
        if not api_key:
            return AgentRunResult(
                status="infra_error",
                error_message=f"missing API key: {api_key_env}",
                wall_time_s=time.perf_counter() - t0,
            )
        # `AIBENCH_BASE_URL` is documented as an alias of `OPENAI_BASE_URL` in `.env.example`
        # and `REFERENCE.md:269`, and this was the one adapter that never read it — so an
        # operator who set only the alias got `https://api.openai.com/v1` and a per-case HTTP
        # error rather than a message saying the gateway was not configured.
        base_url = (
            model.base_url
            or os.environ.get("OPENAI_BASE_URL")
            or os.environ.get("AIBENCH_BASE_URL")
            or "https://api.openai.com/v1"
        ).rstrip("/")
        # Config wins over env, same precedence as base_url above (see openai_compat).
        model_name = model.model or os.environ.get("OPENAI_MODEL")
        max_tokens = int(self.agent_config.options.get("max_tokens", model.max_tokens))
        allow_bash = bool(self.agent_config.options.get("allow_bash", True))

        system = self.agent_config.options.get("system_prompt") or (
            "You are a coding agent. You may call tools by returning JSON only:\n"
            '{"tool":"list|read|write|bash|submit","path":"...","content":"...","command":"...","message":"..."}\n'
            "Use list/read/write to edit files under the workspace. bash runs in workspace. "
            "When done, tool=submit."
        )
        messages: list[dict[str, str]] = [
            {"role": "system", "content": system},
            {
                "role": "user",
                "content": (
                    f"Task:\n{case.prompt}\n\n"
                    f"Workspace root: {workspace}\n"
                    "Solve the task by editing files, then submit."
                ),
            },
        ]
        steps: list[StepRecord] = []
        usage = UsageRecord()
        written: list[str] = []

        for step in range(max_steps):
            if time.perf_counter() - t0 > max_wall_time_s:
                return AgentRunResult(
                    status="timeout",
                    artifacts={"files_written": written},
                    usage=usage,
                    steps=steps,
                    wall_time_s=time.perf_counter() - t0,
                    empty_patch=len(written) == 0,
                    error_message="max_wall_time exceeded",
                )
            from aibench.retry import retry_call

            try:

                def _llm() -> dict[str, Any]:
                    with httpx.Client(timeout=request_timeout_s(max_wall_time_s)) as client:
                        resp = client.post(
                            f"{base_url}/chat/completions",
                            headers={
                                "Authorization": f"Bearer {api_key}",
                                "Content-Type": "application/json",
                            },
                            json={
                                "model": model_name,
                                "temperature": model.temperature,
                                "max_tokens": max_tokens,
                                "messages": messages,
                            },
                        )
                        resp.raise_for_status()
                        return resp.json()

                body = retry_call(_llm, label=f"tool_loop_llm:{case.case_id}:step{step}")
            except Exception as e:
                return AgentRunResult(
                    status="infra_error",
                    error_message=f"LLM failed after retries: {e}",
                    usage=usage,
                    steps=steps,
                    wall_time_s=time.perf_counter() - t0,
                    empty_patch=len(written) == 0,
                )

            u = body.get("usage") or {}
            usage.prompt_tokens += int(u.get("prompt_tokens") or 0)
            usage.completion_tokens += int(u.get("completion_tokens") or 0)
            usage.total_tokens += int(
                u.get("total_tokens")
                or (int(u.get("prompt_tokens") or 0) + int(u.get("completion_tokens") or 0))
            )
            usage.model_calls += 1

            choice = body["choices"][0]
            msg = choice["message"]
            content = str(msg.get("content") or "").strip()
            if not content and str(choice.get("finish_reason") or "") != "length":
                # Only when the model finished: a truncated reply puts thinking in the
                # reasoning stream, and treating that as the answer makes the loop argue with
                # its own transcript. This anchor is the strong end of every calibration panel,
                # so a systematic misread here biases every difficulty measurement.
                content = str(msg.get("reasoning_content") or "").strip()
            steps.append(StepRecord(step_index=step, action="llm_call", tool="chat"))
            try:
                data = _loads_tolerant(_strip_fences(content))
            except Exception:
                messages.append({"role": "assistant", "content": content})
                messages.append(
                    {
                        "role": "user",
                        "content": 'Invalid JSON. Return only {"tool":...}',
                    }
                )
                continue

            tool = str(data.get("tool") or "").lower()
            if tool == "submit":
                steps.append(
                    StepRecord(
                        step_index=step, action="submit", detail=str(data.get("message") or "")
                    )
                )
                return AgentRunResult(
                    status="completed",
                    artifacts={
                        "files_written": written,
                        "final_message": data.get("message"),
                    },
                    usage=usage,
                    steps=steps,
                    wall_time_s=time.perf_counter() - t0,
                    empty_patch=len(written) == 0,
                )

            obs = self._run_tool(tool, data, workspace, allow_bash=allow_bash, written=written)
            steps.append(StepRecord(step_index=step, action="tool", tool=tool, detail=obs[:200]))
            messages.append({"role": "assistant", "content": json.dumps(data, ensure_ascii=False)})
            messages.append({"role": "user", "content": f"Tool result:\n{obs[:4000]}"})

        return AgentRunResult(
            status="timeout",
            artifacts={"files_written": written},
            usage=usage,
            steps=steps,
            wall_time_s=time.perf_counter() - t0,
            empty_patch=len(written) == 0,
            error_message="max_steps exceeded",
        )

    def _run_tool(
        self,
        tool: str,
        data: dict[str, Any],
        workspace: Path,
        *,
        allow_bash: bool,
        written: list[str],
    ) -> str:
        if tool == "list":
            rel = str(data.get("path") or ".")
            target = (workspace / rel).resolve()
            if not target.is_relative_to(workspace.resolve()):
                return "error: path escape"
            if not target.exists():
                return "error: not found"
            if target.is_file():
                return target.name
            names = sorted(p.name for p in target.iterdir())[:200]
            return "\n".join(names) or "(empty)"
        if tool == "read":
            rel = str(data.get("path") or "")
            target = (workspace / rel).resolve()
            if not target.is_relative_to(workspace.resolve()):
                return "error: path escape"
            if not target.is_file():
                return "error: not a file"
            return target.read_text(encoding="utf-8", errors="replace")[:20000]
        if tool == "write":
            rel = str(data.get("path") or "")
            target = (workspace / rel).resolve()
            if not target.is_relative_to(workspace.resolve()):
                return "error: path escape"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(str(data.get("content") or ""), encoding="utf-8")
            written.append(rel)
            return f"wrote {rel}"
        if tool == "bash":
            if not allow_bash:
                return "error: bash disabled"
            cmd = str(data.get("command") or "")
            refusal = check_bash_command(cmd, allowed=self.allowed_commands)
            if refusal:
                return refusal
            try:
                proc = subprocess.run(
                    cmd,
                    shell=True,
                    cwd=workspace,
                    capture_output=True,
                    text=True,
                    timeout=30,
                    check=False,
                )
            except Exception as e:
                return f"error: {e}"
            out, err = (proc.stdout or "")[-2000:], (proc.stderr or "")[-1000:]
            return f"exit={proc.returncode}\n{out}\n{err}"
        return f"error: unknown tool {tool}"
