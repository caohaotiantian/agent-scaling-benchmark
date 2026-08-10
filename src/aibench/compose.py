"""Build retrieval cases (T4) by planting a verified case among unrelated verified files.

Generators do not produce multi-file projects: a forced-T4 run yielded 0 of 10, blocked on
file count and distractor count. But the capability T4 measures — can a solver find the broken
file among many — does not require the generator to invent the surrounding project. It only
requires the surrounding files to be plausible code that is irrelevant to the fix, and a
verified case set is full of exactly that.

Composition preserves both validity gates by construction: the host's stub, tests and reference
solution are untouched, and the added files cannot participate in the fix. They go in a
subdirectory so they cannot shadow the host's imports, and none of them is a test file, so no
runner collects them.
"""

from __future__ import annotations

from typing import Any

from aibench.cases import is_case_json_path
from aibench.io_util import load_json

#: Where donor files land. A subdirectory keeps them off the import path the host's tests use.
DISTRACTOR_DIR = "vendor"


def donor_files(case: dict[str, Any]) -> list[dict[str, str]]:
    """Implementation files of a case that are safe to plant elsewhere as noise.

    A donor's own stub is included even though its reference solution changes it. "Part of the
    solution" is a property of the *host* case, not the donor: planted under ``vendor/<id>/``
    the file is unreachable from the host's imports and cannot participate in the host's fix,
    so it is exactly the plausible-but-irrelevant code a retrieval case needs.

    Excluding it measured badly — a typical case has one implementation file and that file is
    what its own solution fixes, so 109 of 126 cases had nothing to donate at all.

    Tests are still excluded: a donated test file would be collected and run.
    """
    return [
        {"path": str(f.get("path")), "content": str(f.get("content") or "")}
        for f in (case.get("context") or {}).get("files") or []
        if f.get("role") in {"impl", None}
    ]


def compose_case(
    host: dict[str, Any],
    donors: list[dict[str, Any]],
    *,
    target_files: int = 6,
) -> dict[str, Any]:
    """Return ``host`` with donor files planted alongside it as distractors."""
    composed = dict(host)
    ctx = dict(composed.get("context") or {})
    files = [dict(f) for f in ctx.get("files") or []]
    taken = {f["path"] for f in files}

    added = 0
    for donor in donors:
        if len(files) >= target_files:
            break
        donor_id = str(donor.get("case_id") or "donor")
        for f in donor_files(donor):
            if len(files) >= target_files:
                break
            name = f["path"].rsplit("/", 1)[-1]
            path = f"{DISTRACTOR_DIR}/{donor_id}/{name}"
            if path in taken:
                continue
            files.append({"path": path, "content": f["content"], "role": "distractor"})
            taken.add(path)
            added += 1

    ctx["files"] = files
    composed["context"] = ctx
    meta = dict(composed.get("metadata") or {})
    meta["composed_from"] = [str(d.get("case_id")) for d in donors]
    meta["distractors_added"] = added
    composed["metadata"] = meta
    composed["case_id"] = f"{host.get('case_id')}-retrieval"
    return composed


def load_verified_cases(directory: Any) -> list[dict[str, Any]]:
    """Cases in a directory that passed their audit, newest schema shape assumed."""
    out: list[dict[str, Any]] = []
    for path in sorted(directory.glob("*.json")):
        if not is_case_json_path(path):
            continue
        raw = load_json(path)
        if raw.get("case_id") and (raw.get("metadata") or {}).get("validity_ok") is not False:
            out.append(raw)
    return out


def compose_case_set(
    cases: list[dict[str, Any]],
    *,
    target_files: int = 6,
    donors_per_case: int = 3,
    donor_pool: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Compose every case in ``cases`` against a rotating selection from ``donor_pool``.

    The pool defaults to ``cases`` but is worth separating: hosts should be the cases
    calibration kept, while donors only need to be plausible code. Drawing both from the
    curated set starves composition, since selection is what made that set small.

    Donors rotate rather than being drawn at random so the output is reproducible: a case set
    whose contents change between runs cannot be compared against an earlier calibration.
    """
    pool = donor_pool if donor_pool is not None else cases
    if not cases or len(pool) < 2:
        return []
    composed: list[dict[str, Any]] = []
    for i, host in enumerate(cases):
        donors: list[dict[str, Any]] = []
        for j in range(len(pool)):
            candidate = pool[(i + 1 + j) % len(pool)]
            if candidate.get("case_id") == host.get("case_id"):
                continue
            donors.append(candidate)
            if len(donors) >= donors_per_case:
                break
        result = compose_case(host, donors, target_files=target_files)
        if result["metadata"]["distractors_added"]:
            composed.append(result)
    return composed
