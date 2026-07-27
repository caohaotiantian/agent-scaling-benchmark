"""Build minimal workspace snapshot dirs from case context.files."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from aibench.cases import case_set_dir
from aibench.io_util import load_json, write_json


def build_snapshot_for_case(
    case: dict[str, Any],
    *,
    case_set: str,
    update_case_json: bool = True,
) -> Path:
    """Write context.files into snapshots/<case_id>/ and optionally set workspace.mode=mixed."""
    cid = case["case_id"]
    base = case_set_dir(case_set)
    snap = base / "snapshots" / cid
    snap.mkdir(parents=True, exist_ok=True)
    for f in (case.get("context") or {}).get("files") or []:
        rel = str(f.get("path") or "file.txt").lstrip("/")
        if ".." in Path(rel).parts:
            continue
        path = snap / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(f.get("content") or ""), encoding="utf-8")

    if update_case_json:
        ctx = case.setdefault("context", {})
        ws = dict(ctx.get("workspace") or {})
        if not ws.get("mode") or ws.get("mode") == "inline":
            ws["mode"] = "mixed"
        ws["snapshot"] = {"path": f"snapshots/{cid}"}
        ws["strict"] = bool(ws.get("strict", True))
        ctx["workspace"] = ws
        # Keep files as overlays (may be empty later); retain for now
        out = base / f"{cid}.json"
        write_json(out, case)
    return snap


def build_snapshots_for_case_set(case_set: str) -> dict[str, Any]:
    base = case_set_dir(case_set)
    if not base.is_dir():
        raise FileNotFoundError(base)
    built = []
    for p in sorted(base.glob("*.json")):
        case = load_json(p)
        snap = build_snapshot_for_case(case, case_set=case_set, update_case_json=True)
        built.append({"case_id": case.get("case_id"), "snapshot": str(snap)})
    return {"case_set": case_set, "count": len(built), "items": built}
