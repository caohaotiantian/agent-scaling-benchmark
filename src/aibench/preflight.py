"""Assert the machine can produce a comparable measurement, before it spends anything.

Every external version this harness depends on used to live in a comment. `configs/agents/
opencode.yaml` said "measured against 1.18.15" and nothing installed it, pinned it, checked it
or recorded which version produced a number — for the adapter the project explicitly recommends
over `tool_loop` for all model comparisons. node's floor was documented as "≥ 22" and is really
22.18, a difference that flips five real cases from fail to pass. Python's interpreter was
unpinned and `uv.lock` forks numpy on it.

A comment cannot fail. This can:

    uv run python -m aibench doctor
"""

from __future__ import annotations

import functools
import platform
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from aibench.io_util import repo_root
from aibench.languages import MIN_NODE_VERSION, unsupported_node_reason

#: Read from `.python-version` rather than hard-coded, so the pin has one home.
_PYTHON_VERSION_FILE = ".python-version"


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    found: str | None
    expected: str
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "ok": self.ok,
            "found": self.found,
            "expected": self.expected,
            "detail": self.detail,
        }


def _pinned_python() -> str | None:
    path = repo_root() / _PYTHON_VERSION_FILE
    return path.read_text(encoding="utf-8").strip() if path.is_file() else None


@functools.lru_cache(maxsize=1)
def opencode_version() -> str | None:
    """``opencode --version``, or ``None`` when the binary is absent or unreadable.

    Cached: the adapter checks it once per attempt per case and `provenance.environment()`
    once per artifact, and each call was a 20-second-timeout subprocess. The binary cannot
    change under a running process in any way that should be measured mid-run.
    """
    if shutil.which("opencode") is None:
        return None
    try:
        out = subprocess.run(
            ["opencode", "--version"], capture_output=True, text=True, timeout=20, check=True
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    match = re.search(r"\d+\.\d+\.\d+", out)
    return match.group(0) if match else (out.strip() or None)


def pinned_opencode_version() -> str | None:
    """The version `configs/agents/opencode.yaml` pins, if it pins one."""
    from aibench.io_util import load_yaml

    path = repo_root() / "configs/agents/opencode.yaml"
    if not path.is_file():
        return None
    options = load_yaml(path).get("options") or {}
    pinned = options.get("expected_version")
    return str(pinned) if pinned else None


def check_python() -> Check:
    found = ".".join(str(n) for n in sys.version_info[:2])
    pinned = _pinned_python()
    return Check(
        name="python",
        ok=pinned is None or found == pinned,
        found=found,
        expected=pinned or "any (>=3.11)",
        detail=(
            ""
            if pinned is None or found == pinned
            else f"`uv.lock` resolves a different numpy on {found} than on {pinned}"
        ),
    )


def check_node() -> Check:
    reason = unsupported_node_reason()
    from aibench.languages import node_version

    found = node_version()
    return Check(
        name="node",
        ok=reason is None,
        found=".".join(str(n) for n in found) if found else None,
        expected=">= " + ".".join(str(n) for n in MIN_NODE_VERSION),
        detail=reason or "",
    )


def check_opencode() -> Check:
    found = opencode_version()
    pinned = pinned_opencode_version()
    if found is None:
        return Check(
            name="opencode",
            ok=False,
            found=None,
            expected=pinned or "installed",
            detail="not on PATH; six run configs and the whole anchor panel need it",
        )
    return Check(
        name="opencode",
        ok=pinned is None or found == pinned,
        found=found,
        expected=pinned or "any (unpinned)",
        detail=(
            ""
            if pinned is None or found == pinned
            else "a different scaffold version is a different instrument"
        ),
    )


def check_grading_env() -> Check:
    from aibench.grading_env import unsatisfied_promises

    missing = unsatisfied_promises()
    return Check(
        name="grading-env",
        ok=not missing,
        found="satisfied" if not missing else f"missing {', '.join(missing)}",
        expected="every name in configs/grading-env.yaml importable",
        detail="" if not missing else "run: uv sync --extra dev --extra grading",
    )


def check_sandbox() -> Check:
    """macOS-only. A Linux replay measures a different instrument with no boundary at all."""
    from aibench.agents.opencode import _SANDBOX_EXEC

    available = Path(_SANDBOX_EXEC).is_file()
    return Check(
        name="opencode-sandbox",
        ok=available,
        found=platform.system(),
        expected="sandbox-exec present (macOS)",
        detail=(
            ""
            if available
            else "opencode will run unconfined; the run records sandboxed=false and refuses "
            "unless AIBENCH_ALLOW_UNSANDBOXED=1"
        ),
    )


def run_checks() -> list[Check]:
    return [check_python(), check_node(), check_grading_env(), check_opencode(), check_sandbox()]


def render(checks: list[Check]) -> str:
    width = max(len(c.name) for c in checks)
    lines = []
    for c in checks:
        mark = "ok  " if c.ok else "FAIL"
        lines.append(
            f"{mark} {c.name:<{width}}  found={c.found or '-'}  expected={c.expected}"
            + (f"\n     {c.detail}" if c.detail else "")
        )
    return "\n".join(lines)
