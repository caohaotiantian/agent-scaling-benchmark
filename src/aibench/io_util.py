from __future__ import annotations

import contextlib
import json
import os
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
