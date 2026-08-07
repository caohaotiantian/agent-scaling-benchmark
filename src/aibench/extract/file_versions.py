"""Reconstruct the before and after of a file from what a trace actually did to it.

The generator collapses difficulty: measured across three independent interventions, nothing
about the input predicts the difficulty of the case it produces — case size correlates at
+0.07, suppressing prompt disclosure moved it not at all, and source-trace complexity comes in
under |r| = 0.225 with the sign backwards. Given a trace where an engineer edited eight files
across eleven commands, the model still writes a two-file, thirty-line self-contained exercise.

So the defect has to stop being the model's invention. A trace's `edit` calls carry
``filePath``, ``oldString`` and ``newString``; replaying them against the file contents the
trace read gives the real state before the fix and the real state after it.

Replay is strictly ordered and only ever matches against content the trace has actually seen.
Searching every snapshot in the trace for ``oldString`` finds far more matches — 88.8% against
the 26.2% this achieves — but a match against the wrong version reconstructs a "before" state
that never existed, and a case built on it would be fiction with real code in it.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from aibench.extract.history_parse import extract_files_from_tool_text, parse_jsonish
from aibench.languages import registered_spec, spec_for_path

_EDIT_TOOLS = {"edit", "str_replace", "str_replace_editor"}
_WRITE_TOOLS = {"write", "file_write"}
_PATH_KEYS = ("filePath", "file_path", "path", "target_file")


@dataclass
class FileVersion:
    """A file as the trace found it and as the trace left it."""

    path: str
    pre: str
    post: str
    edits: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {"path": self.path, "pre": self.pre, "post": self.post, "edits": self.edits}


@dataclass
class ReplayStats:
    """What replay managed and what it could not, so yield is never guessed at."""

    edits_seen: int = 0
    edits_applied: int = 0
    unlocatable: int = 0
    dropped_unregistered: int = 0
    dropped_unparseable: int = 0
    reasons: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "edits_seen": self.edits_seen,
            "edits_applied": self.edits_applied,
            "unlocatable": self.unlocatable,
            "dropped_unregistered": self.dropped_unregistered,
            "dropped_unparseable": self.dropped_unparseable,
        }


_PY_IMPORT = re.compile(r"^[ \t]*(?:from|import)[ \t]+([A-Za-z_][\w]*)", re.M)
_JS_IMPORT = re.compile(r"""(?:from|import)\s+['"]([^'"]+)['"]""")
_JS_REQUIRE = re.compile(r"""require\(\s*['"]([^'"]+)['"]""")


def unsatisfiable_imports(path: str, content: str) -> set[str]:
    """Imports this file needs that a one-file workspace cannot provide.

    A case is graded with the file under test and a test file, nothing else. A real file from
    a real repository routinely imports numpy, or a sibling module from its own tree, and
    neither is there at grading time.

    That is not merely lost yield. Measured on 22 reverse-constructed cases, an unsatisfiable
    import let five of them pass the stub gate for the wrong reason: the pre-edit file imported
    numpy and the post-edit file did not, so the tests separated the two versions by which
    packages happened to be installed rather than by the defect. Every one of the 16 cases with
    an unsatisfiable import failed the corrected gate, and the one case that survived it had
    none — so this predicate is worth applying before paying for generation, not after.
    """
    if path.endswith(".py"):
        import importlib.util
        import sys

        out: set[str] = set()
        for mod in _PY_IMPORT.findall(content or ""):
            if mod in sys.stdlib_module_names:
                continue
            try:
                if importlib.util.find_spec(mod) is None:
                    out.add(mod)
            except (ImportError, ValueError):
                # A parent package that itself fails to import is no more available than a
                # missing one; either way the suite cannot be collected.
                out.add(mod)
        return out
    if path.endswith((".js", ".mjs", ".cjs", ".ts")):
        specs = set(_JS_IMPORT.findall(content or "")) | set(_JS_REQUIRE.findall(content or ""))
        # `node:` builtins ship with the runtime. Everything else is either a package that
        # needs node_modules or a relative path to a sibling file the case does not carry.
        return {s for s in specs if not s.startswith("node:")}
    return set()


def _first(args: dict[str, Any], keys: tuple[str, ...]) -> str:
    for k in keys:
        v = args.get(k)
        if isinstance(v, str) and v.strip():
            return v
    return ""


def _call(tc: Any) -> tuple[str, dict[str, Any]]:
    if not isinstance(tc, dict):
        return "", {}
    fn = tc.get("function") if isinstance(tc.get("function"), dict) else None
    name = str((fn or {}).get("name") or tc.get("name") or "").strip().lower()
    args = parse_jsonish((fn or {}).get("arguments", tc.get("arguments")))
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except json.JSONDecodeError:
            args = {}
    return name, args if isinstance(args, dict) else {}


#: Trailing path component, because tool output reports paths the caller may not match exactly.
def _key(path: str) -> str:
    return re.split(r"[\\/]", path.strip())[-1]


def replay_file_versions(
    messages: list[dict[str, Any]],
    *,
    languages_only: bool = True,
    require_parse: bool = True,
) -> tuple[list[FileVersion], ReplayStats]:
    """Walk a normalised trace and return the files it demonstrably changed.

    ``languages_only`` keeps files this harness can execute. Real traces edit far more C++ and
    Rust than Python, and registering a toolchain that cannot run makes every case built from
    it fail at grading time on every configuration equally — which reads as difficulty rather
    than as a broken case.
    """
    stats = ReplayStats()
    seen: dict[str, str] = {}
    original: dict[str, str] = {}
    current: dict[str, FileVersion] = {}

    for msg in messages:
        if msg.get("role") == "tool":
            for f in extract_files_from_tool_text(str(msg.get("content") or "")):
                k = _key(f["path"])
                seen.setdefault(k, f["content"])
                original.setdefault(k, f["content"])

        for tc in msg.get("tool_calls") or []:
            name, args = _call(tc)
            path = _first(args, _PATH_KEYS)
            if not path:
                continue
            k = _key(path)

            if name in _WRITE_TOOLS:
                body = args.get("content")
                if isinstance(body, str):
                    seen[k] = body
                continue

            if name not in _EDIT_TOOLS:
                continue
            stats.edits_seen += 1
            old, new = args.get("oldString"), args.get("newString")
            if not isinstance(old, str) or not old:
                stats.unlocatable += 1
                continue
            base = seen.get(k)
            if base is None or old not in base:
                # The trace never showed us this file in the state the edit expects. Guessing
                # would fabricate a "before" that never existed.
                stats.unlocatable += 1
                continue
            seen[k] = base.replace(old, new if isinstance(new, str) else "", 1)
            stats.edits_applied += 1
            fv = current.get(k)
            if fv is None:
                current[k] = FileVersion(
                    path=path, pre=original.get(k, base), post=seen[k], edits=1
                )
            else:
                fv.post = seen[k]
                fv.edits += 1

    out: list[FileVersion] = []
    for fv in current.values():
        if fv.pre == fv.post:
            continue
        spec = spec_for_path(fv.path) or registered_spec(None)
        if languages_only and spec_for_path(fv.path) is None:
            stats.dropped_unregistered += 1
            continue
        # A `pre` that never parsed fails for reasons the defect had nothing to do with, and
        # the stub gate cannot tell the two apart.
        unparseable = spec is not None and (
            spec.parses(fv.pre) is False or spec.parses(fv.post) is False
        )
        if require_parse and unparseable:
            stats.dropped_unparseable += 1
            continue
        out.append(fv)
    return out, stats
