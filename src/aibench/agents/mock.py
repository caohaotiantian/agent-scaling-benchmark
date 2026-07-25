from __future__ import annotations

import time
from pathlib import Path

from aibench.agents.base import AgentAdapter
from aibench.models import AgentRunResult, Case, StepRecord, UsageRecord

# Deterministic solutions for seed cases (harness validation only).
_SEED_SOLUTIONS: dict[str, dict[str, str]] = {
    "seed-v0-001-fizzbuzz": {
        "fizzbuzz.py": (
            "def fizzbuzz(n: int) -> list[str]:\n"
            "    out: list[str] = []\n"
            "    for i in range(1, n + 1):\n"
            "        if i % 15 == 0:\n"
            "            out.append('FizzBuzz')\n"
            "        elif i % 3 == 0:\n"
            "            out.append('Fizz')\n"
            "        elif i % 5 == 0:\n"
            "            out.append('Buzz')\n"
            "        else:\n"
            "            out.append(str(i))\n"
            "    return out\n"
        )
    },
    "seed-v0-002-fix-avg": {
        "stats.py": (
            "def average(nums: list[float]) -> float:\n"
            "    if not nums:\n"
            "        return 0.0\n"
            "    return sum(nums) / len(nums)\n"
        )
    },
    "seed-v0-003-normalize-name": {
        "util.py": (
            "import re\n\n\n"
            "def normalize_name(s: str) -> str:\n"
            "    s = s.strip().lower()\n"
            '    return re.sub(r"\\s+", " ", s)\n'
        )
    },
    "seed-v0-004-snapshot-div": {
        "calc.py": (
            "def add(a: int, b: int) -> int:\n"
            "    return a + b\n\n\n"
            "def mul(a: int, b: int) -> int:\n"
            "    return a * b\n\n\n"
            "def div(a: int, b: int):\n"
            "    if b == 0:\n"
            "        return None\n"
            "    return a // b\n"
        )
    },
}


class MockAgent(AgentAdapter):
    def run(
        self,
        case: Case,
        workspace: Path,
        *,
        max_steps: int,
        max_wall_time_s: float,
    ) -> AgentRunResult:
        t0 = time.perf_counter()
        solve_seed = bool(self.agent_config.options.get("solve_seed", True))
        steps = [
            StepRecord(step_index=0, action="read_prompt", detail=case.case_id),
            StepRecord(step_index=1, action="write_files", tool="fs"),
        ]

        written: list[str] = []
        if solve_seed and case.case_id in _SEED_SOLUTIONS:
            for rel, content in _SEED_SOLUTIONS[case.case_id].items():
                path = workspace / rel
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8")
                written.append(rel)
            empty_patch = False
            message = "mock solved seed case"
        else:
            # Intentionally leave workspace unchanged → likely grade fail.
            empty_patch = True
            message = "mock no-op (solve_seed false or unknown case)"

        wall = time.perf_counter() - t0
        return AgentRunResult(
            status="completed",
            artifacts={
                "files_written": written,
                "final_message": message,
                "patch": None,
            },
            usage=UsageRecord(
                prompt_tokens=10,
                completion_tokens=20 if written else 0,
                total_tokens=30 if written else 10,
                model_calls=1,
            ),
            steps=steps[: max(1, min(max_steps, len(steps)))],
            wall_time_s=wall,
            empty_patch=empty_patch,
        )
