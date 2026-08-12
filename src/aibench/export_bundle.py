"""Export a shareable case bundle, with provenance decided by machine rather than memory.

Generated cases arrive in one directory from two paths that look identical on disk. Measured
over a 575-case build against the drafts that produced it: the 541 cases the LLM wrote share
1.7% of their substantive lines with the private drafts, and every one of those matches is
boilerplate like ``from __future__ import annotations``. The 34 that fell back to
``heuristic_case_from_draft`` share **100%** — that function deep-copies the draft, so those
cases *are* the production code, internal paths and all.

Nothing distinguishes them by eye, so the provenance check is a gate here rather than a note in
a runbook. Everything that fails any gate is excluded and its reason recorded, and that check
has no override: a switch that skips it would eventually be used on a deadline, which is the
exact situation it exists for.

Reverse-constructed cases are a separate matter, and ``--allow-production-derived`` is not an
exception to the above. Those cases ship the file as the trace found it and as the trace left
it, so they are production source *by design* rather than by accident — measured across 8 of
them, 0% to 91.2% of their substantive lines appear verbatim in the private drafts (measured
over the 31 shipped cases; an earlier 13%-76% came from a sample of 8). There is no
version of them that is not production code, so a gate can only ask whether the owner intends
to send that out. The flag records that intent; it skips nothing, every other gate still
applies, and the manifest names each source project and its measured share so whoever receives
the bundle knows what they are holding.
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


def _draft_source_lines(draft: dict[str, Any]) -> list[str]:
    """Every substantive line a draft carries, including the ones the overlap gate could not see.

    A draft holds production source in two places: `context.files`, and `metadata.file_versions`
    — the real before and after of an edit, which is exactly what reverse construction ships as
    the stub and the reference solution. Indexing only the first made the gate blind to the
    second: measured on 8 reverse-constructed cases it reported 2.4%-23.9% verbatim while the
    true overlap with production source was 0%-91.2%. The one gate that exists to answer "is
    this production code?" could not see the production code.
    """
    out = _substantive_lines(draft)
    for fv in (draft.get("metadata") or {}).get("file_versions") or []:
        if not isinstance(fv, dict):
            continue
        for key in ("pre", "post"):
            for line in str(fv.get(key) or "").splitlines():
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
            index.update(_draft_source_lines(load_json(p)))
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
    allow_production_derived: bool = False,
) -> str | None:
    """Why this case cannot ship, or ``None`` if it can."""
    if validator is not None and list(validator.iter_errors(case)):
        return "schema"
    meta = case.get("metadata") or {}
    generation = str(meta.get("generation") or "")
    if generation == "reverse" and not allow_production_derived:
        # Not a mislabelling to wave through: reverse construction ships the file as the trace
        # found it and as it left it, so these cases ARE production source by design, at a
        # measured 0%-91.2% verbatim. Whether that may leave the building is the owner's call
        # and not a default, so it is named for what it is rather than filed under "provenance".
        return "production_derived"
    if generation not in ("llm", "reverse"):
        # The heuristic path deep-copies the draft, so these cases are production code. This
        # one has no override and is not meant to acquire one.
        return "provenance"
    if require_audit and not meta.get("validity_ok"):
        return "audit"
    if scan_case_dict(case):
        return "secrets"
    share = verbatim_share(case, draft_lines)
    if share > max_verbatim and generation != "reverse":
        return f"verbatim:{share:.3f}"
    # For a reverse case the share is not a leak detector — high is the expected reading, and
    # enforcing a 5% threshold on cases that are 0%-91.2% verbatim by construction would reject
    # every one while implying the survivors had been sanitised. It is recorded instead.
    return None


def export_bundle(
    *,
    source_set: str,
    output_dir: Path,
    drafts_dir: Path | None = None,
    max_verbatim: float = DEFAULT_MAX_VERBATIM,
    require_audit: bool = True,
    dry_run: bool = False,
    allow_production_derived: bool = False,
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
    derived: list[dict[str, Any]] = []
    for path in sorted(p for p in src.glob("*.json") if is_case_json_path(p)):
        case = load_json(path)
        cid = str(case.get("case_id") or path.stem)
        reason = _reject_reason(
            case,
            validator=validator,
            draft_lines=draft_lines,
            max_verbatim=max_verbatim,
            require_audit=require_audit,
            allow_production_derived=allow_production_derived,
        )
        if reason:
            rejected.setdefault(reason.split(":")[0], []).append(cid)
            continue
        accepted.append((cid, case))
        meta = case.get("metadata") or {}
        if str(meta.get("generation") or "") == "reverse":
            # Recorded per case, because "this bundle contains production source" is not
            # actionable while "this case is 76% of KernHLight/server.py" is.
            derived.append(
                {
                    "case_id": cid,
                    "language": case.get("language"),
                    "source": meta.get("reverse_source") or meta.get("reverse_source_path"),
                    "verbatim_share": round(verbatim_share(case, draft_lines), 3),
                }
            )

    if not dry_run:
        output_dir.mkdir(parents=True, exist_ok=True)
        for cid, case in accepted:
            write_json(output_dir / f"{cid}.json", _scrubbed(case))

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
    if derived:
        manifest["production_derived"] = {
            "acknowledged": True,
            "what_this_means": (
                "These cases carry source from real repositories verbatim: the stub is a file "
                "as an engineer found it and the reference solution is that file as they left "
                "it. verbatim_share is the fraction of each case's substantive lines found in "
                "the private drafts. Treat this bundle as production source."
            ),
            "count": len(derived),
            "max_verbatim_share": max(d["verbatim_share"] for d in derived),
            "cases": sorted(derived, key=lambda d: -d["verbatim_share"]),
        }
    if not dry_run:
        write_json(output_dir / "MANIFEST.json", manifest)
    return manifest


#: Audit detail is a runner transcript, so it carries the absolute path of whatever temporary
#: workspace this machine happened to use — `/Users/<name>/...` in every failure message.
#: Useful locally, and nothing a recipient can act on.
_AUDIT_DETAIL_KEYS = ("validity_issues", "validity_checks")


def _scrubbed(case: dict[str, Any]) -> dict[str, Any]:
    """A copy without the local-machine detail the audit left in metadata."""
    import copy

    out = copy.deepcopy(case)
    meta = out.get("metadata")
    if isinstance(meta, dict):
        for key in _AUDIT_DETAIL_KEYS:
            meta.pop(key, None)
    return out


def _count(rows: list[tuple[str, dict[str, Any]]], key: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for _, case in rows:
        k = str((case.get("metadata") or {}).get(key) or "unset")
        out[k] = out.get(k, 0) + 1
    return dict(sorted(out.items()))
