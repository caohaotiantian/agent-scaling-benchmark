#!/usr/bin/env python3
"""Check that a reconstructed file is the file, and report where the material comes from.

Reverse construction rests on one claim: the stub is the file as the trace found it. The read
tool appends a line describing its own output, and keeping that line as content broke the claim
three ways at once — it made Python unparseable so the material was discarded, it let a window
of a file pass as the whole file, and it shipped a stub that would not even load.

``--self-test`` asserts the predicate itself against inline fixtures, so it runs anywhere and
fails on a tree where the fix is absent. The pool and A/B modes need data that is gitignored or
behind ``AIBENCH_DB_URL``; both report that they were skipped rather than passing silently.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aibench.extract.file_versions import (
    PRE_FROM_READ,
    PRE_FROM_TOOL_WRITE,
    replay_file_versions,
)
from aibench.extract.history_parse import (
    READ_COMPLETE,
    READ_PARTIAL,
    extract_files_from_tool_text,
)
from aibench.extract.reverse_case import iter_file_versions

#: Every shape the read tool emits, enumerated over each line of the 23,868 files reconstructed
#: for the `_rev_raw4` pool. Four report a fragment; the first reports a whole file, and lies
#: about it 6.3% of the time, which is why the declared count is checked rather than believed.
FOOTERS = [
    ("(End of file - total 2 lines)", READ_COMPLETE),
    ("(Showing lines 1-2 of 190. Use offset=3 to continue.)", READ_PARTIAL),
    ("(File has more lines. Use 'offset' parameter to read beyond line 2000)", READ_PARTIAL),
    ("(Output capped at 50 KB. Showing lines 1-2. Use offset=3 to continue.)", READ_PARTIAL),
    (
        "(Output truncated at 51200 bytes. Use 'offset' parameter to read beyond line 2)",
        READ_PARTIAL,
    ),
    ("(End of file - total 593 lines)", READ_PARTIAL),  # declared count disagrees with the body
]

BODY = "def total(items):\n    return sum(items)\n"


def _tool_text(path: str, body: str) -> str:
    return f"<path>{path}</path>\n<type>file</type>\n<content>{body}</content>"


def _read(path: str, body: str) -> dict:
    return {"role": "tool", "content": _tool_text(path, body), "tool_calls": None}


def _call(name: str, args: dict) -> dict:
    return {
        "role": "assistant",
        "content": "",
        "tool_calls": [{"function": {"name": name, "arguments": json.dumps(args)}}],
    }


def self_test() -> int:
    failures: list[str] = []

    def check(label: str, cond: bool) -> None:
        print(f"  {'ok  ' if cond else 'FAIL'}  {label}")
        if not cond:
            failures.append(label)

    print("footer recognition")
    for footer, expected in FOOTERS:
        (f,) = extract_files_from_tool_text(_tool_text("calc.py", f"{BODY}\n{footer}"))
        check(f"{footer[:52]:<52} -> {expected}", f["origin"] == expected)
        check(f"{'  footer removed from content':<52}", footer not in f["content"])

    print("a complete read parses once the footer is gone")
    (f,) = extract_files_from_tool_text(_tool_text("calc.py", f"{BODY}\n{FOOTERS[0][0]}"))
    try:
        ast.parse(f["content"])
        parsed = True
    except SyntaxError:
        parsed = False
    check("ast.parse succeeds", parsed)

    print("provenance of the reconstructed before-state")
    fvs, st = replay_file_versions(
        [
            _read("a.py", f"x = 1\ny = 2\n\n{FOOTERS[0][0]}"),
            _call("edit", {"filePath": "a.py", "oldString": "x = 1", "newString": "x = 9"}),
        ]
    )
    check("a full read vouches for `pre`", [f.pre_origin for f in fvs] == [PRE_FROM_READ])

    fvs, st = replay_file_versions(
        [
            _read("a.py", f"x = 1\ny = 2\n\n{FOOTERS[1][0]}"),
            _call("edit", {"filePath": "a.py", "oldString": "x = 1", "newString": "x = 9"}),
        ]
    )
    check("a window yields no pair at all", fvs == [])
    check("and says so", st.dropped_partial_read == 1 and st.unlocatable == 1)

    fvs, _ = replay_file_versions(
        [
            _call("write", {"filePath": "a.py", "content": "x = 1\n"}),
            _call("edit", {"filePath": "a.py", "oldString": "x = 1", "newString": "x = 9"}),
        ]
    )
    check(
        "a `pre` the tool wrote is labelled", [f.pre_origin for f in fvs] == [PRE_FROM_TOOL_WRITE]
    )

    draft = {"metadata": {"file_versions": [f.to_dict() for f in fvs]}}
    check("and is not offered as material", iter_file_versions(draft) == [])

    print(f"\n{'PASS' if not failures else 'FAIL'}: {len(failures)} failing check(s)")
    return 1 if failures else 0


def pool_report(pool: Path) -> int:
    """Where the material in an existing draft pool came from. Never a pass/fail verdict."""
    if not pool.is_dir():
        print(f"pool report: skipped, {pool} not found")
        return 0
    origins: Counter[str] = Counter()
    usable: set[str] = set()
    drafts_with_usable = 0
    n = 0
    for p in sorted(pool.glob("*.json")):
        if p.name.startswith("_"):
            continue
        try:
            draft = json.loads(p.read_text())
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
        n += 1
        for fv in (draft.get("metadata") or {}).get("file_versions") or []:
            origins[str(fv.get("pre_origin") or "unlabelled")] += 1
        keep = iter_file_versions(draft)
        if keep:
            drafts_with_usable += 1
        for fv in keep:
            usable.add(
                hashlib.sha1(
                    f"{fv.get('pre')}\0{fv.get('post')}".encode(errors="replace")
                ).hexdigest()
            )
    print(f"pool report: {pool} ({n} drafts)")
    print(f"  pre provenance: {dict(origins.most_common())}")
    print(f"  drafts with >=1 usable pair: {drafts_with_usable}")
    print(f"  deduplicated usable pairs:   {len(usable)}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--pool",
        type=Path,
        default=None,
        help="A draft directory to describe. Gitignored, so absence is reported, not failed.",
    )
    args = ap.parse_args()
    rc = self_test()
    if args.pool is not None:
        rc = pool_report(args.pool) or rc
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
