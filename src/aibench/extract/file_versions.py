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

import ast
import json
import re
from dataclasses import dataclass, field
from typing import Any

from aibench.extract.history_parse import (
    READ_COMPLETE,
    READ_PARTIAL,
    READ_UNKNOWN,
    extract_files_from_tool_text,
    parse_jsonish,
)
from aibench.languages import registered_spec, spec_for_path

_EDIT_TOOLS = {"edit", "str_replace", "str_replace_editor"}
_WRITE_TOOLS = {"write", "file_write"}
_PATH_KEYS = ("filePath", "file_path", "path", "target_file")


#: `pre` is the file as a read showed it in full — the only provenance the reverse-construction
#: argument actually covers.
PRE_FROM_READ = "read_complete"
#: `pre` is what a `write` call put there. Either no read happened at all, or the trace wrote the
#: file first and only read it back afterwards, which shows the tool its own output. The
#: "defect" is then a flaw in code the model itself authored. 182 of the 737 pairs in the
#: current draft pool are of the first kind.
PRE_FROM_TOOL_WRITE = "tool_write"
#: A read with no footer, so the tool never said whether it returned the whole file.
PRE_FROM_UNLABELLED_READ = "read_unlabelled"
#: Two files in the trace share a basename, and replay keys on basenames — so the read that
#: would vouch for this `pre` may have been of the other file.
PRE_FROM_AMBIGUOUS_PATH = "ambiguous_path"

#: How a read's own report maps onto what it vouches for.
_READ_ORIGIN = {READ_COMPLETE: PRE_FROM_READ, READ_UNKNOWN: PRE_FROM_UNLABELLED_READ}

#: `post` is the file after the last edit replay could locate.
POST_FROM_EDIT = "edit"
#: `post` is what a later `write` call put there. Replay used to stop updating `post` once an
#: edit had created the pair, so a trace that edited a file and then rewrote it whole shipped the
#: *intermediate* state as the reference solution while `pre_origin` still read `read_complete` —
#: a gold file that was never the file as the engineer left it.
POST_FROM_TOOL_WRITE = "tool_write"


@dataclass
class FileVersion:
    """A file as the trace found it and as the trace left it."""

    path: str
    pre: str
    post: str
    edits: int = 0
    pre_origin: str = PRE_FROM_READ
    post_origin: str = POST_FROM_EDIT

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "pre": self.pre,
            "post": self.post,
            "edits": self.edits,
            "pre_origin": self.pre_origin,
            "post_origin": self.post_origin,
        }


@dataclass
class ReplayStats:
    """What replay managed and what it could not, so yield is never guessed at."""

    edits_seen: int = 0
    edits_applied: int = 0
    unlocatable: int = 0
    dropped_unregistered: int = 0
    dropped_unparseable: int = 0
    #: Reads that returned a window of a file rather than the file, and were therefore not
    #: admitted as its content. Expect `unlocatable` to rise with this: an edit that used to be
    #: matched against a fragment is now reported as unmatched, which is the honest answer.
    dropped_partial_read: int = 0
    reasons: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "edits_seen": self.edits_seen,
            "edits_applied": self.edits_applied,
            "unlocatable": self.unlocatable,
            "dropped_unregistered": self.dropped_unregistered,
            "dropped_unparseable": self.dropped_unparseable,
            "dropped_partial_read": self.dropped_partial_read,
        }


#: Node builtins are importable bare, so flagging them would drop pairs that grade fine.
_NODE_BUILTINS = frozenset(
    [
        "assert",
        "buffer",
        "child_process",
        "cluster",
        "console",
        "constants",
        "crypto",
        "dgram",
        "dns",
        "domain",
        "events",
        "fs",
        "http",
        "http2",
        "https",
        "inspector",
        "module",
        "net",
        "os",
        "path",
        "perf_hooks",
        "process",
        "punycode",
        "querystring",
        "readline",
        "repl",
        "stream",
        "string_decoder",
        "timers",
        "tls",
        "tty",
        "url",
        "util",
        "v8",
        "vm",
        "worker_threads",
        "zlib",
    ]
)

#: The leading-dot alternative comes first and is deliberate. Without it the pattern's
#: ``[A-Za-z_]`` simply failed to match ``from .config import X``, so a relative import was not
#: extracted at all and the file read as having no unsatisfiable imports — while the JavaScript
#: side has always caught the same construct. The two channels were being held to different
#: standards, and a flat one-file workspace can satisfy neither.
_PY_IMPORT = re.compile(r"^[ \t]*(?:from|import)[ \t]+(\.+[\w.]*|[A-Za-z_][\w]*)", re.M)
_JS_IMPORT = re.compile(r"""(?:from|import)\s+['"]([^'"]+)['"]""")
_JS_REQUIRE = re.compile(r"""require\(\s*['"]([^'"]+)['"]""")


def unsatisfiable_imports(path: str, content: str) -> set[str]:
    """Imports this file needs that the grading environment cannot provide.

    A case is graded with the file under test, a test file, and whatever
    ``configs/grading-env.yaml`` promises. A real file from a real repository routinely imports
    numpy, or a sibling module from its own tree; the first is a declaration away, the second is
    not there at all.

    That is not merely lost yield. Measured on 22 reverse-constructed cases, an unsatisfiable
    import let five of them pass the stub gate for the wrong reason: the pre-edit file imported
    numpy and the post-edit file did not, so the tests separated the two versions by which
    packages happened to be installed rather than by the defect. Every one of the 16 cases with
    an unsatisfiable import failed the corrected gate, and the one case that survived it had
    none — so this predicate is worth applying before paying for generation, not after.

    Availability comes from the manifest, never from ``find_spec`` on the current interpreter.
    That call resolves implicit namespace packages, so a trace's ``import src...`` was satisfied
    by this repository's own ``src/`` whenever the root was importable: the same draft pool
    survived 24 pairs from the repository root and 21 from anywhere else, which meant the set of
    cases built depended on the working directory.
    """
    from aibench.grading_env import is_available

    if path.endswith(".py"):
        out: set[str] = set()
        for mod in _PY_IMPORT.findall(content or ""):
            if mod.startswith("."):
                # A sibling module from the file's own package. There is no package at grading
                # time, so this raises `attempted relative import with no known parent package`
                # before a single test runs.
                out.add(mod)
                continue
            if not is_available("python", mod):
                out.add(mod)
        return out
    if path.endswith((".js", ".mjs", ".cjs", ".ts")):
        specs = set(_JS_IMPORT.findall(content or "")) | set(_JS_REQUIRE.findall(content or ""))
        # Builtins ship with the runtime, whether or not they carry the `node:` prefix —
        # `require("fs")` resolves exactly as `require("node:fs")` does. Everything else is
        # either a package the manifest has to promise or a sibling file the case does not carry.
        return {
            s
            for s in specs
            if not s.startswith("node:")
            and s.split("/")[0] not in _NODE_BUILTINS
            and not is_available("javascript", s)
        }
    return set()


def _strip_py_comments(source: str) -> str | None:
    """Source with comments, docstrings and formatting normalised away, or None if unparseable.

    ``ast.unparse`` drops comments and rewrites layout on its own; docstrings survive as bare
    string expressions, so they are removed explicitly.
    """
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError, RecursionError):
        return None
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if not isinstance(body, list) or not body:
            continue
        if not isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        first = body[0]
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
            and len(body) > 1
        ):
            del body[0]
    try:
        return ast.unparse(tree)
    except (AttributeError, ValueError, RecursionError):
        # A deeply nested expression blows the stack inside `unparse`, and this runs from
        # `iter_file_versions`, which `cli.py` calls outside its per-draft try — so an escaping
        # RecursionError takes the whole build down rather than skipping one draft.
        return None


#: Punctuation after which a ``/`` opens a regex literal rather than dividing.
_REGEX_PRECEDERS = set("=(,:[!&|?{};+-*%<>~^")

#: Keywords after which the same is true. Without these, ``return /^\//.test(p)`` parses as a
#: division, its ``\/\/`` reads as a line comment, and the rest of the line disappears — from
#: *both* versions, so an edit that changed only the surviving part is reported as
#: comment-only. Measured: that turns a genuine ``&&`` → ``||`` fix into "not a defect".
_REGEX_KEYWORDS = frozenset(
    [
        "return",
        "typeof",
        "instanceof",
        "in",
        "of",
        "new",
        "delete",
        "void",
        "throw",
        "case",
        "do",
        "else",
        "yield",
        "await",
    ]
)

_TRAILING_WORD = re.compile(r"([A-Za-z_$][\w$]*)$")

#: Stands in for a template literal's body while whitespace is normalised around it. Template
#: literals carry their whitespace as data — indentation inside a multi-line SQL or prompt
#: string is part of the value — and collapsing it made "reindent this template" read as an
#: edit that changed nothing. 73% of this corpus is TypeScript, much of it prompt templates.
_TEMPLATE_SLOT = "\x00tpl{}\x00"


def _strip_js_comments(source: str) -> tuple[str, list[str]] | None:
    """Comments removed, template bodies parked aside. None when the scan cannot be trusted."""
    out: list[str] = []
    templates: list[str] = []
    i, n = 0, len(source)
    while i < n:
        ch = source[i]
        if ch in "\"'":
            j = i + 1
            while j < n and source[j] != ch:
                j += 2 if source[j] == "\\" else 1
            if j >= n:
                return None  # unterminated string: the rest of the scan is guesswork
            out.append(source[i : j + 1])
            i = j + 1
            continue
        if ch == "`":
            j, depth = i + 1, 0
            while j < n and (depth or source[j] != "`"):
                if source[j] == "\\":
                    j += 2
                    continue
                if source[j : j + 2] == "${":
                    depth += 1
                    j += 2
                    continue
                if depth and source[j] == "}":
                    depth -= 1
                j += 1
            if j >= n:
                return None
            templates.append(source[i : j + 1])
            out.append(_TEMPLATE_SLOT.format(len(templates) - 1))
            i = j + 1
            continue
        if ch == "/" and i + 1 < n:
            nxt = source[i + 1]
            if nxt == "/":
                while i < n and source[i] != "\n":
                    i += 1
                continue
            if nxt == "*":
                end = source.find("*/", i + 2)
                if end < 0:
                    return None  # unterminated block comment would swallow the whole file
                i = end + 2
                continue
            before = "".join(out).rstrip()
            word = _TRAILING_WORD.search(before)
            is_regex = (
                not before
                or before[-1] in _REGEX_PRECEDERS
                or (word is not None and word.group(1) in _REGEX_KEYWORDS)
            )
            if is_regex:
                j = i + 1
                while j < n and source[j] not in "/\n":
                    j += 2 if source[j] == "\\" else 1
                if j < n and source[j] == "/":
                    out.append(source[i : j + 1])
                    i = j + 1
                    continue
        out.append(ch)
        i += 1
    return "".join(out), templates


def _normalize_ws(text: str) -> str:
    return "\n".join(line.strip() for line in text.splitlines() if line.strip())


def defect_is_not_semantic(path: str, pre: str, post: str) -> bool:
    """True when the edit changed only comments, docstrings or layout.

    Such an edit is not a defect fix, but reverse construction happily builds a case from it and
    both validity gates pass — a test that greps the source separates the two versions on the
    comment alone. That is exactly how ``rev-9029660c5a575277`` and ``rev-ac427816b0ed447b`` came
    to ship: byte-identical implementation and reference solution, p_hat 1.00 against 0.00, and
    the "defect" was a tidied file header.

    Conservative by construction: anything this cannot analyse (unparseable Python, an unknown
    language) returns False. Failing to reject a comment-only edit costs one bad candidate that
    later gates may still catch; rejecting a real defect loses material outright.
    """
    if pre == post:
        return True
    if path.endswith(".py"):
        a, b = _strip_py_comments(pre), _strip_py_comments(post)
        return a is not None and b is not None and a == b
    if path.endswith((".js", ".mjs", ".cjs", ".ts", ".jsx", ".tsx", ".mts", ".cts")):
        a, b = _strip_js_comments(pre), _strip_js_comments(post)
        if a is None or b is None:
            return False
        # Template bodies are compared byte for byte; only the code around them is normalised.
        return _normalize_ws(a[0]) == _normalize_ws(b[0]) and a[1] == b[1]
    return False


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


def _same_file(a: str, b: str) -> bool:
    """Whether two spellings of a path can be the same file, on the evidence available.

    The two sides are not written alike: `extract_files_from_tool_text` keeps only the last
    three components of an absolute path and only the basename of a Windows one, while an edit
    call carries the path in full. So they are compared over the components they *both* have —
    `/home/u/proj/calc.py` and `ubuntu/proj/calc.py` agree wherever they overlap.

    When one side is a bare basename there is nothing to compare and this answers True. That
    is right for the ordinary case — one file spelled two ways — and it is only safe because
    the paths compared here are the ones the trace actually wrote. `extract_files_from_tool_text`
    reduces a Windows path to its basename, so two genuinely different files under one name
    used to collapse into a single entry and this check saw nothing to compare; it now records
    `source_path` and the untruncated spellings reach this comparison.
    """
    pa = [c for c in re.split(r"[\\/]", a.strip()) if c and c != "."]
    pb = [c for c in re.split(r"[\\/]", b.strip()) if c and c != "."]
    n = min(len(pa), len(pb))
    return n > 0 and pa[-n:] == pb[-n:]


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
    #: What `original[k]` is evidence of. Absent from `original` means nothing vouched for it.
    vouched: dict[str, str] = {}
    #: Keys the trace wrote before it ever read them. Reading a file back after writing it shows
    #: the tool's own output, so such a read is not evidence of what an engineer found there.
    written_first: set[str] = set()
    #: Distinct paths sharing a basename. `_key` collapses them, so a read of one would
    #: otherwise stand in as the "before" of the other — and now carry a provenance stamp
    #: saying a read vouched for it.
    paths_for_key: dict[str, set[str]] = {}
    current: dict[str, FileVersion] = {}

    for msg in messages:
        if msg.get("role") == "tool":
            for f in extract_files_from_tool_text(str(msg.get("content") or "")):
                if f.get("origin") == READ_PARTIAL:
                    # A window is content the trace saw *part* of. Matching an edit against it
                    # can only reconstruct a "before" that is a fragment wearing a whole file's
                    # name — the fiction this module exists to refuse. `rev-461e8d91390e3915`
                    # shipped exactly that: 40 lines of a 190-line file, cut mid-expression.
                    stats.dropped_partial_read += 1
                    continue
                k = _key(f["path"])
                paths_for_key.setdefault(k, set()).add(f.get("source_path") or f["path"])
                if k not in original:
                    vouched[k] = (
                        PRE_FROM_TOOL_WRITE
                        if k in written_first
                        else _READ_ORIGIN.get(str(f.get("origin")), PRE_FROM_UNLABELLED_READ)
                    )
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
                    if k not in original:
                        written_first.add(k)
                    seen[k] = body
                    # A write *after* an edit is the state the engineer left the file in, and
                    # replay used to leave `post` on the intermediate edit — so the reference
                    # solution was a version that never existed as a finished file. Recorded as
                    # such rather than silently folded in: a `post` the trace wrote wholesale is
                    # weaker evidence than one replay reconstructed edit by edit.
                    fv = current.get(k)
                    if fv is not None and fv.post != body:
                        fv.post = body
                        fv.post_origin = POST_FROM_TOOL_WRITE
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
                    path=path,
                    pre=original.get(k, base),
                    post=seen[k],
                    edits=1,
                    # Nothing in `original` means `pre` came through the `base` fallback, i.e.
                    # from a `write` call's arguments.
                    pre_origin=vouched.get(k, PRE_FROM_TOOL_WRITE),
                )
                paths_for_key.setdefault(k, set()).add(path)
            else:
                fv.post = seen[k]
                fv.edits += 1

    out: list[FileVersion] = []
    for k, fv in current.items():
        paths = sorted(paths_for_key.get(k, ()))
        if any(not _same_file(a, b) for a in paths for b in paths):
            # Two genuinely different files share a basename, so the read that would vouch for
            # this `pre` may have been of the other one. Withdraw the claim rather than guess.
            fv.pre_origin = PRE_FROM_AMBIGUOUS_PATH
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
