"""Scientific validity gates for benchmark cases."""

from __future__ import annotations

import hashlib
import re
import shutil
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from aibench.cases import case_set_dir, load_cases
from aibench.grading import grade_case
from aibench.io_util import load_json, write_json
from aibench.models import Case
from aibench.workspace import materialize_workspace


@dataclass
class ValidityIssue:
    code: str
    severity: str  # error | warn
    message: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CaseValidityReport:
    case_id: str
    ok: bool
    issues: list[ValidityIssue] = field(default_factory=list)
    difficulty: str | None = None
    fingerprint: str | None = None
    checks: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "ok": self.ok,
            "difficulty": self.difficulty,
            "fingerprint": self.fingerprint,
            "checks": self.checks,
            "issues": [i.to_dict() for i in self.issues],
        }


def case_fingerprint(case: Case | dict[str, Any]) -> str:
    if isinstance(case, Case):
        prompt = case.prompt
        paths = sorted(f.path for f in case.files)
        task_type = case.task_type
    else:
        prompt = str(case.get("prompt") or "")
        paths = sorted(
            f.get("path") or ""
            for f in ((case.get("context") or {}).get("files") or [])
        )
        task_type = str(case.get("task_type") or "")
    basis = f"{task_type}|{prompt.strip()}|{'|'.join(paths)}"
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:16]


def estimate_difficulty(case: Case) -> str:
    n_files = len(case.files)
    loc = sum(len((f.content or "").splitlines()) for f in case.files)
    test_fns = 0
    for f in case.files:
        if "test" in f.path:
            test_fns += len(re.findall(r"^\s*def\s+test_", f.content or "", re.M))
    score = n_files + test_fns + loc // 40
    if score <= 4:
        return "easy"
    if score <= 12:
        return "medium"
    return "hard"


def _context_blob(case: Case) -> str:
    return "\n".join(f"{f.path}\n{f.content}" for f in case.files)


def check_contamination(case: Case) -> list[ValidityIssue]:
    issues: list[ValidityIssue] = []
    blob = _context_blob(case)
    g = case.grader
    # gold file contents fully present in context → likely solution leak
    for gf in g.gold_files:
        body = (gf.content or "").strip()
        if len(body) >= 40 and body in blob:
            issues.append(
                ValidityIssue(
                    "contamination_gold_in_context",
                    "error",
                    f"gold file {gf.path} content already present in context",
                )
            )
    for line in g.key_lines:
        s = (line or "").strip()
        if len(s) < 8:
            continue
        if s in blob and g.mode == "gold":
            # for gold graders, key line in context means trivial pass
            issues.append(
                ValidityIssue(
                    "contamination_keyline_in_context",
                    "error",
                    f"key_line already in context: {s[:60]}",
                )
            )
    # obvious solution markers in prompt
    if re.search(r"```[\s\S]{80,}```", case.prompt) and "implement" in case.prompt.lower():
        issues.append(
            ValidityIssue(
                "prompt_contains_large_code_fence",
                "warn",
                "prompt embeds large code fence; check leakage",
            )
        )
    return issues


def check_stub_fails(case: Case, *, case_set: str | None = None) -> tuple[bool, str]:
    """Return (ok, detail). ok=True means stub correctly fails (gate passed)."""
    if case.grader.mode != "script" or not case.grader.command:
        return True, "skipped_non_script"
    tmp = Path(tempfile.mkdtemp(prefix="aibench_audit_"))
    try:
        ws = tmp / "workspace"
        csd = case_set_dir(case_set) if case_set else None
        materialize_workspace(case, ws, case_set_dir=csd, allow_network=False)
        grade = grade_case(case, ws)
        if grade.infra_error:
            return False, f"infra: {grade.detail}"
        if grade.passed:
            return False, "stub_passed_grader"
        return True, "stub_failed_as_expected"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def audit_case(case: Case, *, case_set: str | None = None) -> CaseValidityReport:
    issues: list[ValidityIssue] = []
    fp = case_fingerprint(case)
    difficulty = estimate_difficulty(case)
    checks: dict[str, Any] = {"fingerprint": fp, "difficulty": difficulty}

    issues.extend(check_contamination(case))

    stub_ok, stub_detail = check_stub_fails(case, case_set=case_set)
    checks["stub_fail"] = {"ok": stub_ok, "detail": stub_detail}
    if not stub_ok:
        issues.append(
            ValidityIssue("stub_fail_gate", "error", f"stub must fail grader: {stub_detail}")
        )

    if case.grader.mode == "script" and case.metadata.get("weak_grader"):
        issues.append(
            ValidityIssue("weak_grader_flag", "warn", "metadata.weak_grader=true with script mode")
        )

    # empty / trivial prompt
    if len((case.prompt or "").strip()) < 20:
        issues.append(ValidityIssue("prompt_too_short", "error", "prompt too short"))

    errors = [i for i in issues if i.severity == "error"]
    return CaseValidityReport(
        case_id=case.case_id,
        ok=len(errors) == 0,
        issues=issues,
        difficulty=difficulty,
        fingerprint=fp,
        checks=checks,
    )


def audit_case_set(case_set: str) -> dict[str, Any]:
    cases = load_cases(case_set, validate=True)
    reports = [audit_case(c, case_set=case_set) for c in cases]
    fps: dict[str, list[str]] = {}
    for r in reports:
        fps.setdefault(r.fingerprint or "", []).append(r.case_id)
    dupes = {k: v for k, v in fps.items() if k and len(v) > 1}
    for r in reports:
        if r.fingerprint in dupes and len(dupes[r.fingerprint]) > 1:
            r.issues.append(
                ValidityIssue(
                    "duplicate_fingerprint",
                    "warn",
                    f"duplicate of {dupes[r.fingerprint]}",
                )
            )
            # duplicates are warn only unless exact same id
    ok_n = sum(1 for r in reports if r.ok)
    return {
        "case_set": case_set,
        "total": len(reports),
        "passed": ok_n,
        "failed": len(reports) - ok_n,
        "duplicates": dupes,
        "reports": [r.to_dict() for r in reports],
        "content_fingerprint": _set_fingerprint(cases),
    }


def _set_fingerprint(cases: list[Case]) -> str:
    parts = sorted(f"{c.case_id}:{case_fingerprint(c)}" for c in cases)
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()[:16]


def annotate_case_metadata(case_path: Path, report: CaseValidityReport) -> None:
    raw = load_json(case_path)
    meta = dict(raw.get("metadata") or {})
    meta["difficulty"] = report.difficulty
    meta["fingerprint"] = report.fingerprint
    meta["validity_ok"] = report.ok
    meta["validity_issues"] = [i.to_dict() for i in report.issues]
    raw["metadata"] = meta
    write_json(case_path, raw)
