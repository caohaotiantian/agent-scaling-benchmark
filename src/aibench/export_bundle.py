"""Export a shareable case bundle, with provenance decided by machine rather than memory.

Generated cases arrive in one directory from two paths that look identical on disk. Measured
over a 575-case build against the drafts that produced it: the 541 cases the LLM wrote share
1.7% of their substantive lines with the private drafts, and every one of those matches is
boilerplate like ``from __future__ import annotations``. The 34 that fell back to
``heuristic_case_from_draft`` share **100%** — that function deep-copies the draft, so those
cases *are* the production code, internal paths and all.

Nothing distinguishes them by eye, so the provenance check is a gate here rather than a note in
a runbook. Everything that fails any gate is excluded and its reason recorded, and there is no
override flag: a switch that skips the provenance check would eventually be used on a deadline,
which is the exact situation it exists for.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from aibench.cases import case_set_dir, is_case_json_path, load_schema_validator
from aibench.io_util import load_json, write_json
from aibench.secrets_scan import scan_case_dict

#: A line short enough to be incidental carries no provenance signal; `import os` proves nothing.
_SUBSTANTIVE_LINE = 25
DEFAULT_MAX_VERBATIM = 0.05


def _substantive_lines(case: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for f in (case.get("context") or {}).get("files") or []:
        for line in str(f.get("content") or "").splitlines():
            s = line.strip()
            if len(s) > _SUBSTANTIVE_LINE:
                out.append(s)
    return out


def draft_line_index(drafts_dir: Path) -> set[str]:
    """Every substantive line present in the private drafts, for the overlap check."""
    index: set[str] = set()
    if not drafts_dir.is_dir():
        return index
    for p in sorted(drafts_dir.glob("*.json")):
        if not is_case_json_path(p):
            continue
        try:
            index.update(_substantive_lines(load_json(p)))
        except (json.JSONDecodeError, OSError):
            continue
    return index


def verbatim_share(case: dict[str, Any], draft_lines: set[str]) -> float:
    """Fraction of a case's substantive lines that appear verbatim in the drafts."""
    lines = _substantive_lines(case)
    if not lines:
        return 0.0
    return sum(1 for s in lines if s in draft_lines) / len(lines)


def _reject_reason(
    case: dict[str, Any],
    *,
    validator: Any,
    draft_lines: set[str],
    max_verbatim: float,
    require_audit: bool,
) -> str | None:
    """Why this case cannot ship, or ``None`` if it can."""
    if validator is not None and list(validator.iter_errors(case)):
        return "schema"
    meta = case.get("metadata") or {}
    if (meta.get("generation") or "") != "llm":
        # The heuristic path deep-copies the draft, so these cases are production code.
        return "provenance"
    if require_audit and not meta.get("validity_ok"):
        return "audit"
    if scan_case_dict(case):
        return "secrets"
    share = verbatim_share(case, draft_lines)
    if share > max_verbatim:
        return f"verbatim:{share:.3f}"
    return None


def export_bundle(
    *,
    source_set: str,
    output_dir: Path,
    drafts_dir: Path | None = None,
    max_verbatim: float = DEFAULT_MAX_VERBATIM,
    require_audit: bool = True,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Copy the cases that pass every gate into ``output_dir`` and write a MANIFEST.

    ``output_dir`` is an ordinary path, deliberately not a case-set name: the bundle is meant
    to leave the repository, and routing it through the case-set namespace would put production
    code one ``git add`` away from the history it must stay out of.
    """
    src = case_set_dir(source_set)
    if not src.is_dir():
        raise FileNotFoundError(f"source case set not found: {src}")

    validator = load_schema_validator()
    draft_lines = draft_line_index(drafts_dir) if drafts_dir else set()
    if drafts_dir and not draft_lines:
        raise ValueError(
            f"no draft lines read from {drafts_dir}; the overlap gate would pass everything. "
            "Point --drafts-dir at the drafts this set was generated from, or omit it only if "
            "you accept shipping without that check."
        )

    accepted: list[tuple[str, dict[str, Any]]] = []
    rejected: dict[str, list[str]] = {}
    for path in sorted(p for p in src.glob("*.json") if is_case_json_path(p)):
        case = load_json(path)
        cid = str(case.get("case_id") or path.stem)
        reason = _reject_reason(
            case,
            validator=validator,
            draft_lines=draft_lines,
            max_verbatim=max_verbatim,
            require_audit=require_audit,
        )
        if reason:
            rejected.setdefault(reason.split(":")[0], []).append(cid)
        else:
            accepted.append((cid, case))

    if not dry_run:
        output_dir.mkdir(parents=True, exist_ok=True)
        for cid, case in accepted:
            write_json(output_dir / f"{cid}.json", case)

    manifest = {
        "source_set": source_set,
        "exported_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "gates": {
            "provenance": "metadata.generation == 'llm'",
            "audit": bool(require_audit),
            "secrets": "scan_case_dict must be empty",
            "max_verbatim_share": max_verbatim,
            "drafts_compared": str(drafts_dir) if drafts_dir else None,
            "draft_lines_indexed": len(draft_lines),
        },
        "considered": len(accepted) + sum(len(v) for v in rejected.values()),
        "exported": len(accepted),
        "rejected": {k: len(v) for k, v in sorted(rejected.items())},
        "rejected_ids": {k: sorted(v) for k, v in sorted(rejected.items())},
        "tier_distribution": _count(accepted, "tier"),
        "case_ids": sorted(cid for cid, _ in accepted),
    }
    if not dry_run:
        write_json(output_dir / "MANIFEST.json", manifest)
    return manifest


def _count(rows: list[tuple[str, dict[str, Any]]], key: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for _, case in rows:
        k = str((case.get("metadata") or {}).get(key) or "unset")
        out[k] = out.get(k, 0) + 1
    return dict(sorted(out.items()))
