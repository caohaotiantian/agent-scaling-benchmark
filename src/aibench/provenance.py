"""What produced a result, recorded so a later reader can tell two runs apart.

Every one of the 148 run manifests on disk carries the same `code_version`:
``aibench@0.1.0 / agent@1.0.0``, both halves literals. An adapter fix that moved the same
model's pass rate by 58 points left no trace in any artifact, so deciding which numbers need
re-measuring came down to reading directory timestamps against `git log`.

The panel identity had the same hole from the other side. `anchor_fingerprint` hashed the three
referenced YAML files, so it was byte-identical before and after that fix — and
``calibrate-cases --reuse-from`` will hand back a p_hat measured by an adapter that no longer
exists whenever the fingerprints agree.
"""

from __future__ import annotations

import functools
import hashlib
import platform
import subprocess
import sys
from pathlib import Path

from aibench.io_util import repo_root

#: Modules whose source decides what a run *means*: how the agent is driven, how a workspace is
#: built, and how a verdict is reached. A change to any of them makes two runs incomparable
#: even when every config file is identical.
HARNESS_SOURCES = (
    "agents",
    "grading.py",
    "workspace.py",
    "runner.py",
    # Reached from the four above and just as decisive. `languages.py` owns `pass_ratio` and the
    # uncollectable/hard split that `grading.py` delegates to — this project's signature
    # distinction. `retry.py` decides how many times a flaky gateway is asked again, which is
    # what turns an outage into a dropped row. `models.py` and `env_config.py` resolve the
    # config and the endpoint a run actually used.
    "languages.py",
    "retry.py",
    "models.py",
    "env_config.py",
    # `calibrate.py` decides which cases are kept and what `p_hat` means; `stats.py` owns
    # `point_biserial`, `wilson_ci` and `cost_curve`. Omitting them let `--reuse-from` carry a
    # `r_pb` across a change to the estimator that produced it.
    "calibrate.py",
    "stats.py",
)


def git_revision() -> str:
    """``<short sha>`` or ``<short sha>-dirty``; ``unknown-worktree`` outside a checkout.

    Never falls back to a literal version string. A field named ``code_version`` that reports a
    constant is worse than no field, because it reads as an answer.
    """
    root = repo_root()
    try:
        # `git rev-parse` walks upward, so a copy of `src/` inside another checkout would
        # cheerfully report that repository's HEAD. Naming the wrong revision is the same
        # failure as naming a literal: it reads as an answer.
        toplevel = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        ).stdout.strip()
        if not toplevel or Path(toplevel).resolve() != root.resolve():
            return "unknown-worktree"
        sha = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return "unknown-worktree"
    if not sha:
        return "unknown-worktree"
    try:
        dirty = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=20,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        # Could not tell. Say so rather than defaulting to the flattering answer.
        return f"{sha}-unknown-cleanliness"
    return f"{sha}-dirty" if dirty else sha


# Four entries. `harness_digest()` and `harness_digest(None)` are distinct cache keys and
# both occur in normal use, so a 2-entry cache is already full before any probe runs and
# each probe evicts a live entry. Correctness is unaffected; this is re-hashing cost.
@functools.lru_cache(maxsize=4)
def harness_digest(source_root: Path | None = None) -> str:
    """Content hash of the code that decides what a run means.

    Sorted by path so the digest does not depend on filesystem order, and computed from bytes
    rather than mtimes so a checkout does not change it.

    ``source_root`` points the hash at a copy of `src/`. It exists so the instrument check can
    prove that editing an adapter moves this digest without editing the adapter it is running
    from — the probe used to write into the live file and restore it in a `finally`, which
    leaves a corrupted working tree if the process dies in between.
    """
    root = (source_root or repo_root() / "src") / "aibench"
    parts: list[bytes] = []
    for entry in HARNESS_SOURCES:
        target = root / entry
        files = sorted(target.rglob("*.py")) if target.is_dir() else [target]
        for path in files:
            if not path.is_file():
                continue
            parts.append(path.relative_to(root).as_posix().encode())
            parts.append(path.read_bytes())
    return hashlib.sha256(b"\0".join(parts)).hexdigest()[:16]


def dependency_digest() -> str | None:
    """Hash of the resolved lock file, so a dependency bump is visible in the artifact."""
    lock = repo_root() / "uv.lock"
    if not lock.is_file():
        return None
    return hashlib.sha256(lock.read_bytes()).hexdigest()[:16]


def node_version() -> str | None:
    try:
        out = subprocess.run(
            ["node", "--version"], capture_output=True, text=True, timeout=10, check=True
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() or None


def venv_digest() -> str | None:
    """Identity of the interpreter's installed packages, without naming this machine.

    Replaces ``python_executable``, which was a home directory in every manifest and told a
    reader nothing they could act on. What matters is *which* packages were importable, and
    that is the same question `dependency_digest` answers for the lock file — but a venv can
    drift from its lock, which is exactly what `uv sync` pruning the grading extra does.
    """
    site = Path(sys.prefix) / "lib"
    if not site.is_dir():
        return None
    names = sorted(
        p.name
        for candidate in site.glob("python*/site-packages")
        for p in candidate.iterdir()
        if p.name.endswith((".dist-info", ".egg-info"))
    )
    if not names:
        return None
    return hashlib.sha256("\n".join(names).encode()).hexdigest()[:16]


def environment() -> dict[str, str | None]:
    """The execution identity to stamp into a manifest.

    ``base_url`` is the gateway a run talked to, and is deliberately the only network detail
    recorded — the API key never enters an artifact. ``python_executable`` and
    ``working_directory`` are gone: both were absolute paths naming an engineer's home
    directory in all 148 manifests, and neither is something a reader can act on.
    """
    from aibench.env_config import openai_settings
    from aibench.preflight import opencode_version

    settings = openai_settings()
    return {
        "code_version": git_revision(),
        "harness_digest": harness_digest(),
        "dependency_digest": dependency_digest(),
        "venv_digest": venv_digest(),
        "python_version": sys.version.split()[0],
        "node_version": node_version(),
        # The adapter the project recommends for every model comparison, whose version left no
        # trace in any artifact until now.
        "opencode_version": opencode_version(),
        "platform": platform.platform(terse=True),
        "gateway_base_url": settings.get("base_url") or None,
    }
