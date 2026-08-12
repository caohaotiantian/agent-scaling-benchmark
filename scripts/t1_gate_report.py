#!/usr/bin/env python3
"""Acceptance check for the cheap static gates: did they fire where they were calibrated?

`audit-cases --fail-on-error` only says a set failed. It cannot say the new gate rejected the
twelve cases it was built for rather than twelve different ones — a detector that both
over-fires and under-fires by the same count looks identical from the exit code. This asserts
the *set*, not the count, and asserts that the five suites a looser pattern misclassified are
still accepted.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

#: The cases whose tests grep the implementation's source, measured on `_revmixed`.
#: JavaScript 11 of 14, Python 1 of 17 — the disease is overwhelmingly on the JS side.
TRANSCRIPTION = {
    "rev-1f1563e5cdce9714",
    "rev-40473af819aab141",
    "rev-452aa920a815b796",
    "rev-45d9a8eedc412aa7",
    "rev-5e56df47ee4f862c",
    "rev-6c31f4fcb26cf2ce",
    "rev-9029660c5a575277",
    "rev-ac427816b0ed447b",
    "rev-be51b11a3ed4f8a7",
    "rev-dd703d1eb36b1620",
    "rev-ea040c6cc651d689",
    "rev-f4f8a7a78fa184cd",
}

#: Suites a single loose pattern also caught, none of which reads the implementation: three
#: write a fixture with `open(..., 'w')` and are excluded by the mode argument, two use
#: `os.fdopen(fd, 'w')` and one calls `urlopen` — those three never match at all, because
#: neither name carries a word boundary before `open`.
MUST_NOT_FIRE = {
    "rev-24a5ff20e2cb6691",
    "rev-429688dd814e45fe",
    "rev-4646d93ae250add0",
    "rev-772a0200c25e3bb1",
    "rev-8aa4cadfc9034cc0",
    "rev-d94dc43d70c2458d",
}

GATE = "test_reads_source_text"


def fired(report: dict) -> set[str]:
    return {
        r["case_id"]
        for r in report.get("reports") or []
        if any(i.get("code") == GATE for i in r.get("issues") or [])
    }


def import_gate_delta(pool: Path) -> tuple[int, int, list[str]]:
    """Pairs surviving the import gate under the old and the fixed `_PY_IMPORT`.

    The plan requires this printed rather than merely measured: the fix changes an existing
    predicate's output, and a silent change to how much material survives is exactly the kind
    of thing that gets attributed to something else three weeks later.

    The absolute counts are only meaningful together with ``sys.path``. ``find_spec`` resolves
    implicit namespace packages, so with the repository root importable, a trace's
    ``import src...`` or ``import tests...`` is satisfied by this project's own directories:
    the same pool yields 24 survivors from the repository root and 21 from anywhere else. Both
    are printed with the condition attached rather than picking one and calling it the number.
    """
    import hashlib
    import importlib.util

    from aibench.extract.file_versions import unsatisfiable_imports

    old_re = re.compile(r"^[ \t]*(?:from|import)[ \t]+([A-Za-z_][\w]*)", re.M)

    def old_unsat(content: str) -> set[str]:
        out = set()
        for mod in old_re.findall(content or ""):
            if mod in sys.stdlib_module_names:
                continue
            try:
                if importlib.util.find_spec(mod) is None:
                    out.add(mod)
            except (ImportError, ValueError):
                out.add(mod)
        return out

    seen: set[str] = set()
    old_ok = new_ok = 0
    newly: list[str] = []
    for path in sorted(pool.glob("*.json")):
        if path.name.startswith("_"):
            continue
        try:
            draft = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for fv in (draft.get("metadata") or {}).get("file_versions") or []:
            src = str(fv.get("path") or "")
            if not src.endswith(".py"):
                continue
            pre, post = str(fv.get("pre") or ""), str(fv.get("post") or "")
            digest = hashlib.sha1(f"{pre}\0{post}".encode()).hexdigest()
            if digest in seen:
                continue
            seen.add(digest)
            was = not (old_unsat(pre) | old_unsat(post))
            now = not (unsatisfiable_imports(src, pre) | unsatisfiable_imports(src, post))
            old_ok += was
            new_ok += now
            if was and not now:
                newly.append(src.replace("\\", "/").rsplit("/", 1)[-1])
    return old_ok, new_ok, newly


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", type=Path, required=True, help="audit-cases --report JSON")
    ap.add_argument(
        "--drafts",
        type=Path,
        default=Path("benchmarks/ai_coding/cases/_rev_raw4"),
        help="Draft pool for the import-gate before/after count",
    )
    args = ap.parse_args()

    report = json.loads(args.report.read_text(encoding="utf-8"))
    present = {r["case_id"] for r in report.get("reports") or []}
    hit = fired(report)
    extra, missing = sorted(hit - TRANSCRIPTION), sorted(TRANSCRIPTION - hit)
    caught_clean = sorted(hit & MUST_NOT_FIRE)
    # Without this the clean-suite check passes on any set that simply does not contain them.
    absent_clean = sorted(MUST_NOT_FIRE - present)

    print(f"case set: {report.get('case_set')}  ({report.get('total')} cases)")
    print(f"{GATE}: {len(hit)} fired, {len(TRANSCRIPTION)} expected")
    for label, ids in (
        ("over-fired on", extra),
        ("missed", missing),
        ("false positive", caught_clean),
        ("clean suites not present in this set", absent_clean),
    ):
        if ids:
            print(f"  {label}: {', '.join(ids)}")

    if args.drafts.is_dir():
        old_ok, new_ok, newly = import_gate_delta(args.drafts)
        root = str(Path.cwd())
        rooted = any(p in ("", ".", root) for p in sys.path)
        print(
            f"import gate on {args.drafts.name}: {old_ok} -> {new_ok} surviving Python pairs "
            f"(deduplicated by (pre, post) content; repo root on sys.path: {rooted})"
        )
        if newly:
            print(f"  newly rejected: {', '.join(sorted(set(newly)))}")
    else:
        print(f"import gate: skipped, {args.drafts} not found")

    ok = not extra and not missing and not caught_clean and not absent_clean
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
