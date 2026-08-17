"""Generic shell/CLI agent adapter (e.g. wrap mini-swe-agent or custom CLI).

Config options:
  command_template: shell string with placeholders {workspace} {prompt_file} {case_id}
  The command should edit files in {workspace}. Exit 0 = completed.
"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

from aibench.agents.base import AgentAdapter
from aibench.models import AgentRunResult, Case, StepRecord, UsageRecord


def _snapshot(workspace: Path) -> dict[str, int]:
    """Path -> content hash, for everything but the prompt file this adapter drops in."""
    out: dict[str, int] = {}
    for path in workspace.rglob("*"):
        if not path.is_file() or path.name == ".aibench_prompt.txt":
            continue
        try:
            out[path.relative_to(workspace).as_posix()] = hash(path.read_bytes())
        except OSError:
            continue
    return out


class ShellAgent(AgentAdapter):
    def run(
        self,
        case: Case,
        workspace: Path,
        *,
        max_steps: int,
        max_wall_time_s: float,
    ) -> AgentRunResult:
        t0 = time.perf_counter()
        tmpl = self.agent_config.options.get("command_template")
        if not tmpl:
            return AgentRunResult(
                status="infra_error",
                error_message="shell agent requires options.command_template",
                wall_time_s=time.perf_counter() - t0,
            )
        prompt_file = workspace / ".aibench_prompt.txt"
        prompt_file.write_text(case.prompt, encoding="utf-8")
        before = _snapshot(workspace)
        cmd = (
            str(tmpl)
            .replace("{workspace}", str(workspace))
            .replace("{prompt_file}", str(prompt_file))
            .replace("{case_id}", case.case_id)
            .replace("{max_steps}", str(max_steps))
        )
        env = os.environ.copy()
        env.update(
            {str(k): str(v) for k, v in (self.agent_config.options.get("env") or {}).items()}
        )
        try:
            proc = subprocess.run(
                cmd,
                shell=True,
                cwd=workspace,
                capture_output=True,
                text=True,
                timeout=max_wall_time_s,
                env=env,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return AgentRunResult(
                status="timeout",
                error_message="shell agent timeout",
                wall_time_s=time.perf_counter() - t0,
            )
        except Exception as e:
            return AgentRunResult(
                status="infra_error",
                error_message=str(e),
                wall_time_s=time.perf_counter() - t0,
            )

        # What the agent *changed*, not what the workspace contains. Listing every file made
        # `empty_patch` permanently false — the flag exists to catch an agent that did nothing,
        # and it could never fire because a case ships files.
        after = _snapshot(workspace)
        written = sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k))
        status = "completed" if proc.returncode == 0 else "failed"
        return AgentRunResult(
            status=status,
            artifacts={
                "files_written": written[:50],
                # Slices, not indices. `(proc.stdout or "")[-2000]` reads *one character* and
                # raises `IndexError: string index out of range` on any output shorter than
                # 2000 bytes — which is every run of a CLI that succeeds quietly. The adapter
                # therefore could not complete a single successful run, and had no test.
                "final_message": (proc.stdout or "")[-2000:],
                "stderr": (proc.stderr or "")[-1000:],
                "exit_code": proc.returncode,
            },
            usage=UsageRecord(model_calls=1, total_tokens=0),
            steps=[
                StepRecord(step_index=0, action="shell", tool="cli", detail=cmd[:200]),
            ],
            wall_time_s=time.perf_counter() - t0,
            empty_patch=len(written) == 0,
            error_message=None if proc.returncode == 0 else f"exit={proc.returncode}",
        )
