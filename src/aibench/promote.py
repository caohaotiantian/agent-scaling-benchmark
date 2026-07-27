"""Promote reviewed candidate cases to a published case set (human-gated)."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from aibench.cases import case_set_dir, is_case_json_path, load_schema_validator, validate_case_set
from aibench.io_util import load_json, write_json
from aibench.secrets_scan import scan_case_dict


def promote_cases(
    *,
    source_set: str = "auto-v0",
    dest_set: str = "prod-v0",
    case_ids: list[str] | None = None,
    require_script: bool = True,
    allow_secrets: bool = False,
    require_audit: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Copy selected cases from source_set to dest_set after checklist checks.

    Does NOT auto-select cases: if case_ids is None, promotes all that pass
    automated gates (schema + optional script grader + secrets clean).
    Human is expected to pass explicit case_ids for real release.
    """
    src = case_set_dir(source_set)
    if not src.is_dir():
        raise FileNotFoundError(f"source case set not found: {src}")
    dest = case_set_dir(dest_set)
    validator = load_schema_validator()

    selected: list[Path] = []
    if case_ids:
        id_set = set(case_ids)
        for p in sorted(src.glob("*.json")):
            if not is_case_json_path(p):
                continue
            raw = load_json(p)
            if raw.get("case_id") in id_set:
                selected.append(p)
        missing = id_set - {load_json(p).get("case_id") for p in selected}
        if missing:
            raise ValueError(f"case_ids not found in {source_set}: {sorted(missing)}")
    else:
        selected = sorted(p for p in src.glob("*.json") if is_case_json_path(p))

    promoted: list[str] = []
    skipped: list[dict[str, Any]] = []

    if not dry_run:
        dest.mkdir(parents=True, exist_ok=True)

    for path in selected:
        raw = load_json(path)
        cid = raw.get("case_id") or path.stem
        errors = sorted(validator.iter_errors(raw), key=lambda e: list(e.path))
        if errors:
            skipped.append({"case_id": cid, "reason": f"schema: {errors[0].message}"})
            continue
        grader = raw.get("grader") or {}
        if require_script and grader.get("mode") != "script":
            skipped.append({"case_id": cid, "reason": "require_script: grader.mode != script"})
            continue
        if raw.get("metadata", {}).get("weak_grader"):
            skipped.append({"case_id": cid, "reason": "weak_grader=true"})
            continue
        findings = scan_case_dict(raw, path=cid)
        if findings and not allow_secrets:
            skipped.append(
                {
                    "case_id": cid,
                    "reason": "secrets_scan",
                    "findings": [f.to_dict() for f in findings[:5]],
                }
            )
            continue

        if require_audit:
            from aibench.models import Case
            from aibench.validity import audit_case

            try:
                v = audit_case(Case.from_dict(raw), case_set=source_set)
            except Exception as e:
                skipped.append({"case_id": cid, "reason": f"audit_error: {e}"})
                continue
            if not v.ok:
                skipped.append(
                    {
                        "case_id": cid,
                        "reason": "validity_audit_failed",
                        "issues": [i.to_dict() for i in v.issues if i.severity == "error"],
                    }
                )
                continue

        # mark published
        meta = dict(raw.get("metadata") or {})
        meta["review_status"] = "published"
        meta["split"] = "test"
        meta["promoted_from"] = source_set
        raw["metadata"] = meta

        if not dry_run:
            write_json(dest / f"{cid}.json", raw)
            # copy sibling snapshot dir if present
            snap_src = src / "snapshots" / cid
            if snap_src.is_dir():
                snap_dst = dest / "snapshots" / cid
                if snap_dst.exists():
                    shutil.rmtree(snap_dst)
                shutil.copytree(snap_src, snap_dst)
        promoted.append(cid)

    report = {
        "source_set": source_set,
        "dest_set": dest_set,
        "dry_run": dry_run,
        "promoted_count": len(promoted),
        "skipped_count": len(skipped),
        "promoted": promoted,
        "skipped": skipped,
        "dest_validate": validate_case_set(dest_set) if (not dry_run and promoted) else [],
    }
    return report
