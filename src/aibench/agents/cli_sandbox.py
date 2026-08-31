"""Confinement and workspace bookkeeping shared by the adapters that drive a real CLI agent.

A production coding agent reads files, writes files and runs shell commands with the
permissions of the process that launched it. Two things follow, and both are the same for
every such adapter, so they live here rather than once per adapter:

* **The boundary is the kernel's.** The case's gold solution and hidden tests sit in this
  repository, and an agent given a shell can walk to them. ``sandbox_profile`` denies that
  subtree; ``sandbox-exec`` enforces it. A second copy of a security boundary is how the two
  copies drift, and only one of them gets the fix.
* **The workspace is mirrored out and back.** The graded workspace lives under ``runs/``,
  inside this checkout, so an agent that resolves its project root by walking up finds the
  whole repository. The adapters copy the workspace somewhere outside any checkout, run
  there, and mirror the result back.

Nothing here knows which agent is being driven. What each adapter does with its own config,
argv and event stream stays in its own module.
"""

from __future__ import annotations

import hashlib
import os
import shutil
from pathlib import Path
from typing import Any

from aibench.io_util import repo_root
from aibench.models import ModelConfig
from aibench.workspace import assert_disposable

#: Directory entries no agent authored: interpreter and test-runner caches, plus the scratch
#: directories the agents themselves keep. Counting them as edits would make ``empty_patch``
#: false for a run that changed nothing but executed pytest.
IGNORED_DIRS = frozenset(
    {"__pycache__", ".pytest_cache", "node_modules", ".git", ".opencode", ".pi"}
)
IGNORED_SUFFIXES = (".pyc", ".pyo")

#: Apple deprecated the binary but it still works, and it is the only kernel-level file
#: boundary available here without a container.
SANDBOX_EXEC = Path("/usr/bin/sandbox-exec")


def snapshot(root: Path) -> dict[str, str]:
    """Content hashes of everything an agent could have authored under ``root``."""
    out: dict[str, str] = {}
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(root)
        if any(part in IGNORED_DIRS for part in rel.parts):
            continue
        if path.suffix in IGNORED_SUFFIXES:
            continue
        out[rel.as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return out


def changed_paths(before: dict[str, str], after: dict[str, str]) -> list[str]:
    """Paths the agent added, rewrote, or deleted."""
    return sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k))


def mirror_into(source: Path, target: Path) -> None:
    """Make ``target`` hold exactly ``source``'s contents, deletions included.

    ``target`` itself is kept, because the grader was handed that path before the agent ran.

    Symlinks are copied as links rather than followed. Following them raised
    ``IsADirectoryError`` on a link to a directory, and by then the loop above had already
    emptied the target -- the graded workspace was destroyed on the way to reproducing it.
    """
    assert_disposable(target)
    for entry in target.iterdir():
        if entry.is_dir() and not entry.is_symlink():
            shutil.rmtree(entry)
        else:
            entry.unlink()
    for entry in source.iterdir():
        destination = target / entry.name
        if entry.is_symlink():
            destination.symlink_to(os.readlink(entry))
        elif entry.is_dir():
            shutil.copytree(entry, destination, symlinks=True)
        else:
            shutil.copy2(entry, destination)


def protected_root() -> Path:
    """The subtree the agent must not read: the whole enclosing git checkout.

    Not ``repo_root()``. This module sits at ``benchmarks/coding`` inside a monorepo whose
    ``.git`` is one level *above* it, so a profile that denied only ``repo_root()`` left the
    object store readable -- and the case's own gold solution and hidden tests are in it.
    Measured under the profile that denied only ``repo_root()``::

        $ sandbox-exec -f p.sb cat  .../cases/_clean2026/rev-<id>.json   -> Operation not permitted
        $ sandbox-exec -f p.sb git -C <monorepo> show HEAD:<same path>   -> the whole case JSON

    Denying ``.git`` alone would not be enough either: loose objects are readable with
    nothing but ``zlib``, and a second checkout of the same tree would be a second route.
    Denying the checkout closes all of them, and costs nothing -- an agent solving a case
    has no business anywhere in it but its own workspace, which is mirrored out of the tree.
    """
    root = repo_root().resolve()
    checkout = checkout_above(root)
    return checkout.resolve() if checkout is not None else root


def sandbox_profile(protected: Path, *, readable: tuple[Path, ...]) -> str:
    """A seatbelt profile that hides ``protected`` from the process and its descendants.

    Narrow on purpose. The threat is one specific thing -- the agent reading the case's own
    gold solution out of this repository -- so the profile denies that subtree and leaves the
    rest of the machine alone. An allowlist sandbox would be stronger and would also have to
    enumerate every path a Python or Node toolchain touches, which is how a sandbox ends up
    disabled in practice.

    ``readable`` are subtrees inside ``protected`` that must stay reachable: the virtualenv,
    because the grader's command is ``python -m pytest -q`` and an agent that cannot run the
    tests is a differently-shaped instrument.
    """
    lines = [
        "(version 1)",
        "(allow default)",
        # Contents, not metadata. Denying `file-read*` also denies `lstat`, and the agents stat
        # the repository on the way up their own PATH -- opencode exited before reaching the
        # gateway with `EPERM: operation not permitted, lstat`. Metadata reveals nothing that
        # matters here; the gold solution is bytes inside a file.
        f'(deny file-read-data file-write* (subpath "{protected}"))',
    ]
    lines += [f'(allow file-read-data (subpath "{path}"))' for path in readable]
    return "\n".join(lines) + "\n"


def checkout_above(path: Path) -> Path | None:
    """The nearest ancestor that is a git checkout, if any."""
    for parent in [path, *path.parents]:
        if (parent / ".git").exists():
            return parent
    return None


def _tool_paths(tool: str, payload: dict[str, Any]) -> list[str]:
    """Filesystem-ish strings a CLI agent's tool call names, for the out-of-workspace check."""
    if tool == "bash":
        return [str(payload.get("command") or "")]
    return [
        str(payload.get(key) or "")
        for key in ("filePath", "path", "pattern", "include", "glob")
        if payload.get(key)
    ]


def escapes_workspace(tool: str, payload: dict[str, Any], workspace: Path) -> bool:
    """True when a tool call names a path that resolves outside the workspace.

    Deliberately independent of any agent's own refusal wording: this counts what was
    *attempted*, so a version that stops denying does not silently read as "clean".

    Three things the first version got wrong, each of which it reported as clean:
    ``../../secret`` was ignored for not starting with ``/``; ``~/secret`` likewise; and a
    prefix test on the raw string called ``/tmp/ws-other/x`` inside ``/tmp/ws``. Tokens are
    expanded and resolved, and containment is asked of the path rather than of the string.
    """
    for text in _tool_paths(tool, payload):
        for raw in text.replace("'", " ").replace('"', " ").replace("=", " ").split():
            token = raw.strip(",;:()")
            if not token or token.startswith("-"):
                continue
            if not (token.startswith(("/", "~", ".")) or "/" in token):
                continue
            candidate = Path(os.path.expanduser(token))
            if not candidate.is_absolute():
                candidate = workspace / candidate
            # Lexical, not filesystem: the path need not exist to have been asked for, and a
            # symlink inside the workspace is the agent's own to follow.
            resolved = Path(os.path.normpath(candidate))
            if not resolved.is_relative_to(workspace):
                return True
    return False


def resolve_endpoint(model: ModelConfig) -> tuple[str, str, str] | str:
    """``(api_key, base_url, model_name)`` for a gateway, or the reason it cannot be assembled.

    The model config wins over the environment, so a multi-model matrix cannot silently run one
    model under several labels -- the defect that made an earlier ablation compare a model to
    itself while the report went on naming two.
    """
    api_key_env = model.api_key_env or "OPENAI_API_KEY"
    api_key = os.environ.get(api_key_env) or os.environ.get("AIBENCH_API_KEY")
    if not api_key:
        return f"missing API key env: {api_key_env} or AIBENCH_API_KEY"
    base_url = (
        model.base_url
        or os.environ.get("OPENAI_BASE_URL")
        or os.environ.get("AIBENCH_BASE_URL")
        or "https://api.openai.com/v1"
    ).rstrip("/")
    model_name = model.model or os.environ.get("OPENAI_MODEL")
    if not model_name:
        return "no model name in model config or OPENAI_MODEL"
    return api_key, base_url, model_name
