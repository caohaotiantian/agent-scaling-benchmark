from __future__ import annotations

import contextlib
import json
import os
import re
import shlex
import uuid
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, TextIO

import yaml


def load_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise ValueError(f"YAML root must be a mapping: {path}")
    return data


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


@contextmanager
def atomic_write(path: Path, *, newline: str | None = None) -> Generator[TextIO, None, None]:
    """Write ``path`` via a temporary file and one ``os.replace``.

    ``path.open("w")`` truncates first, so a process killed between the truncate and the flush
    leaves a zero-length or half-written file that every reader here treats as complete: the
    resume journal's ``is_file()`` test counts it as a written case and refuses to regenerate
    it, and the truncated JSON then fails schema validation when the set is loaded. Replacing
    the whole file in one step means a reader sees either the previous contents or the new
    ones, never a prefix.

    ``newline`` is passed straight through, for `csv`, which writes its own ``\r\n`` and
    doubles the carriage return unless the handle is opened with ``newline=""``.

    Deliberately without ``fsync``: the failure this addresses is the process dying, which
    ``os.replace`` covers on its own. Surviving a machine crash would cost an fsync on every
    per-case artifact, and nothing here is worth that.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex[:8]}.tmp")
    try:
        with tmp.open("w", encoding="utf-8", newline=newline) as handle:
            yield handle
        os.replace(tmp, path)
    finally:
        # A raise inside the body (or in `os.replace`) must not leave the scratch file behind —
        # and cleaning up must not *replace* that exception with its own. An unlink that loses
        # a race, or hits a read-only directory, would otherwise hide the real failure.
        with contextlib.suppress(OSError):
            tmp.unlink(missing_ok=True)


def write_json(path: Path, data: Any) -> None:
    with atomic_write(path) as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    with atomic_write(path) as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def write_text(path: Path, text: str) -> None:
    """``Path.write_text`` with the same all-or-nothing guarantee as :func:`write_json`.

    The reports are artifacts too: a run killed while writing ``report.md`` leaves a file that
    reads as a short report rather than as a missing one.
    """
    with atomic_write(path) as f:
        f.write(text)


#: `\Z`, not `$`: `$` also matches before a trailing newline, which let `"..\n"` through the
#: inner guard while the schema's lookahead refused it — the two layers failing in opposite
#: directions is the one thing having two of them was supposed to prevent.
CASE_ID_PATTERN = re.compile(r"\A[A-Za-z0-9._-]{1,128}\Z")


def safe_case_id(case_id: str) -> str:
    """Confine a case-supplied id that is also a directory name and a file name.

    `run_dir / "cases" / case_id` absorbs an absolute id whole and follows `..` out of the
    run directory, and the first thing `materialize_workspace` does with the result is
    `shutil.rmtree` it. `assert_disposable` refuses only paths that are or contain the
    checkout, so everything else was reachable. Enforced here rather than at each of the
    five places that spell `f"{case_id}.json"`.
    """
    if not CASE_ID_PATTERN.match(case_id or "") or set(case_id) == {"."}:
        raise ValueError(
            f"unsafe case_id {case_id!r}: expected 1-128 chars of [A-Za-z0-9._-], not all dots"
        )
    return case_id


#: Characters that meant something to the shell and no longer do. A case set written when
#: the grader ran through `sh -c` is refused rather than silently reinterpreted: `pytest -q >
#: out` would otherwise become pytest's argument list, failing for a reason unrelated to the
#: one the author would look for. Quotes are absent on purpose — `shlex.split` still groups.
_SHELL_SYNTAX = (";", "&", "|", ">", "<", "`", "$", "(", ")", "*", "?", "~", "\n", "\r")


def safe_command(command: str, *, field: str) -> list[str]:
    """Split a case-supplied command into argv, refusing anything that wanted a shell.

    The command comes from the case JSON, and the case JSON comes from whoever handed you the
    case set. Running it through a shell made that string arbitrary code on the host; splitting
    it here is what lets the caller pass `shell=False`.
    """
    found = [c for c in _SHELL_SYNTAX if c in command]
    if found:
        shown = ", ".join(repr(c) for c in found)
        raise ValueError(f"{field} is not run through a shell; remove {shown} from {command!r}")
    try:
        argv = shlex.split(command)
    except ValueError as e:
        raise ValueError(f"{field} cannot be parsed as a command: {command!r} ({e})") from e
    if not argv:
        raise ValueError(f"{field} is empty: {command!r}")
    return argv


def repo_root() -> Path:
    # src/aibench/io_util.py -> parents[2] = repo root
    return Path(__file__).resolve().parents[2]


def relative_to_repo(path: Path | str) -> str:
    """``path`` as the repository sees it, or unchanged when it lies outside.

    Two tracked calibration files embed
    an absolute path under the author's home directory in `run_dir` and
    结果目录. That is an engineer's home directory published in the repository, and it is also
    useless to a reader: the run directories are gitignored, so the only actionable part of the
    string is the part after the repository root.
    """
    candidate = Path(path)
    try:
        return candidate.resolve().relative_to(repo_root().resolve()).as_posix()
    except (ValueError, OSError):
        return str(path)
