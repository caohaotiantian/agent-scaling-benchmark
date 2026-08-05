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
from aibench.tiers import check_tier_invariants
from aibench.workspace import materialize_workspace, safe_relpath


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
    tier: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "ok": self.ok,
            "difficulty": self.difficulty,
            "tier": self.tier,
            "fingerprint": self.fingerprint,
            "checks": self.checks,
            "issues": [i.to_dict() for i in self.issues],
        }


#: Bumped whenever the basis below changes. It is carried in the fingerprint itself so that a
#: value stored by an older build can never compare equal to one computed now — a reuse gate
#: that silently accepts a stale fingerprint hands back a p_hat measured on different code.
FINGERPRINT_VERSION = "v2"


def _file_digests(entries: Any) -> list[str]:
    out: list[str] = []
    for f in entries or []:
        if isinstance(f, dict):
            path, content = str(f.get("path") or ""), str(f.get("content") or "")
        else:
            path, content = str(getattr(f, "path", "")), str(getattr(f, "content", "") or "")
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]
        out.append(f"{path}:{digest}")
    return sorted(out)


def case_fingerprint(case: Case | dict[str, Any]) -> str:
    """Identify a case by everything a solver can see, contents included.

    Hashing only the prompt and the file *paths* meant a case whose stub and reference
    solution had been replaced wholesale kept its identity, so ``calibrate-cases
    --reuse-from`` returned the previous p_hat for code it had never run.
    """
    if isinstance(case, Case):
        prompt, task_type = case.prompt, case.task_type
        files = case.files
        gold = case.grader.gold_files
        hidden = case.grader.hidden_tests
    else:
        prompt = str(case.get("prompt") or "")
        task_type = str(case.get("task_type") or "")
        files = (case.get("context") or {}).get("files") or []
        grader = case.get("grader") or {}
        gold = grader.get("gold_files") or []
        hidden = grader.get("hidden_tests") or []
    basis = "|".join(
        [
            FINGERPRINT_VERSION,
            task_type,
            prompt.strip(),
            ",".join(_file_digests(files)),
            ",".join(_file_digests(gold)),
            ",".join(_file_digests(hidden)),
        ]
    )
    return f"{FINGERPRINT_VERSION}:{hashlib.sha256(basis.encode('utf-8')).hexdigest()[:16]}"


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


#: Detail prefixes the audit keys off. Defined once so the reported reason and the boolean
#: derived from it cannot drift apart.
STUB_UNCOLLECTABLE = "stub_uncollectable"
REFERENCE_UNCOLLECTABLE = "reference_solution_uncollectable"


def check_stub_fails(
    case: Case,
    *,
    case_set: str | None = None,
    reference_collects: bool | None = None,
) -> tuple[bool, str]:
    """Return (ok, detail). ok=True means stub correctly fails (gate passed).

    ``reference_collects`` says whether the workspace stands up once the reference solution is
    applied: ``True`` when its suite reached a verdict, ``False`` when it could not be
    collected, and ``None`` when there was no reference solution to try.

    Only ``False`` condemns the stub. ``True`` means the workspace is sound and an
    uncollectable stub is the ordinary "implement this" shape — the visible test imports a
    symbol the stub has yet to define. ``None`` is not evidence either way, and reporting it
    as a broken workspace would restate a case already rejected for having no reference
    solution, inflating the count of workspaces this gate claims to have found.
    """
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
        if grade.collection_error and reference_collects is False:
            # The stub is *supposed* to fail, so a workspace that cannot even be collected
            # satisfies this gate by accident. That is how a case whose hidden test does not
            # parse reaches the shipped set looking like a hard one. The reference solution is
            # what separates the two: if applying it makes the suite run, the workspace is
            # sound and only the stub was incomplete.
            return False, f"{STUB_UNCOLLECTABLE}: {grade.detail[:300]}"
        return True, "stub_failed_as_expected"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def check_reference_solution(case: Case, *, case_set: str | None = None) -> tuple[bool, str]:
    """Return (ok, detail). ok=True means the shipped reference solution passes the grader.

    The complement of :func:`check_stub_fails`. Without it a case with a broken hidden test
    fails every configuration and reads as a hard case when it is simply an unsolvable one.
    """
    if case.grader.mode != "script" or not case.grader.command:
        return True, "skipped_non_script"
    if not case.grader.gold_files:
        # Measured: of 18 cases no configuration could solve, 16 had no reference solution,
        # while cases that shipped one were unsolvable only 2 times in 31. Skipping the check
        # for cases that cannot support it is what let those 16 ship — they look like hard
        # cases in the report and are simply broken.
        return False, "no_reference_solution: solvability cannot be verified"
    tmp = Path(tempfile.mkdtemp(prefix="aibench_solve_"))
    try:
        ws = tmp / "workspace"
        csd = case_set_dir(case_set) if case_set else None
        materialize_workspace(case, ws, case_set_dir=csd, allow_network=False)
        for gf in case.grader.gold_files:
            # Generated paths are untrusted input. `ws / "/home/code/x.py"` resolves to the
            # absolute path, so an unsanitised join writes the reference solution onto the
            # host filesystem instead of into the throwaway workspace.
            try:
                rel = safe_relpath(gf.path)
            except ValueError as e:
                return False, f"reference_solution_path_escapes_workspace: {e}"
            target = ws / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(gf.content, encoding="utf-8")
        grade = grade_case(case, ws)
        if grade.infra_error:
            return False, f"infra: {grade.detail}"
        if grade.collection_error:
            # Both verdicts reject the case, but only one of them is a statement about the
            # task. Reporting a missing dependency as `reference_solution_failed` is what
            # makes an unrunnable workspace indistinguishable from an unsolvable task.
            return False, f"{REFERENCE_UNCOLLECTABLE}: {grade.detail[:300]}"
        if not grade.passed:
            return False, f"reference_solution_failed: {grade.detail[:300]}"
        return True, "reference_solution_passed"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def audit_case(
    case: Case,
    *,
    case_set: str | None = None,
    llm_disclosure_check: bool = False,
) -> CaseValidityReport:
    issues: list[ValidityIssue] = []
    fp = case_fingerprint(case)
    difficulty = estimate_difficulty(case)
    checks: dict[str, Any] = {"fingerprint": fp, "difficulty": difficulty}

    issues.extend(check_contamination(case))

    # The reference solution runs first: whether it makes the workspace collectable is what
    # tells an incomplete stub apart from a broken one.
    ref_ok, ref_detail = check_reference_solution(case, case_set=case_set)
    ref_uncollectable = ref_detail.startswith(REFERENCE_UNCOLLECTABLE)
    checks["reference_solution"] = {
        "ok": ref_ok,
        "detail": ref_detail,
        "uncollectable": ref_uncollectable,
    }
    if not ref_ok:
        issues.append(
            ValidityIssue("solvability_gate", "error", f"case must be solvable: {ref_detail}")
        )

    if ref_uncollectable:
        reference_collects: bool | None = False
    elif ref_ok or ref_detail.startswith("reference_solution_failed"):
        # A reference solution that ran — whether it passed or failed — proves the workspace
        # stands up. Anything else (no gold files, a path escape, an infra failure) never
        # reached the grader and says nothing about collectability.
        reference_collects = True
    else:
        reference_collects = None

    stub_ok, stub_detail = check_stub_fails(
        case, case_set=case_set, reference_collects=reference_collects
    )
    checks["stub_fail"] = {
        "ok": stub_ok,
        "detail": stub_detail,
        "uncollectable": stub_detail.startswith(STUB_UNCOLLECTABLE),
    }
    if not stub_ok:
        issues.append(
            ValidityIssue("stub_fail_gate", "error", f"stub must fail grader: {stub_detail}")
        )

    tier = case.tier
    if tier:
        tier_check = check_tier_invariants(case)
        checks["tier"] = tier_check.to_dict()
        for violation in tier_check.violations:
            issues.append(
                ValidityIssue(f"tier_{violation.code}", violation.severity, violation.message)
            )
    else:
        checks["tier"] = None
        issues.append(ValidityIssue("tier_missing", "warn", "metadata.tier is not set"))

    if llm_disclosure_check and tier and tier != "T1":
        from aibench.extract.llm_soft_filter import llm_disclosure_verdict
        from aibench.tiers import find_disclosures, merge_disclosure_findings

        disclosed, reason = llm_disclosure_verdict(case.prompt)
        checks["llm_disclosure"] = {"disclosed": disclosed, "reason": reason}
        for v in merge_disclosure_findings(find_disclosures(case.prompt), disclosed, reason):
            if v.code == "prompt_discloses_defect_llm":
                issues.append(ValidityIssue(f"tier_{v.code}", v.severity, v.message))

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
        tier=tier,
    )


def _is_uncollectable(report: CaseValidityReport, check: str) -> bool:
    entry = (report.checks or {}).get(check)
    return bool(isinstance(entry, dict) and entry.get("uncollectable"))


def audit_case_set(case_set: str, *, llm_disclosure_check: bool = False) -> dict[str, Any]:
    cases = load_cases(case_set, validate=True)
    reports = [
        audit_case(c, case_set=case_set, llm_disclosure_check=llm_disclosure_check) for c in cases
    ]
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
    by_tier: dict[str, int] = {}
    for r in reports:
        by_tier[r.tier or "unset"] = by_tier.get(r.tier or "unset", 0) + 1
    return {
        "case_set": case_set,
        "total": len(reports),
        "passed": ok_n,
        "failed": len(reports) - ok_n,
        # Counted separately because "the workspace does not run" and "the task is hard" are
        # the same number otherwise, and the second is a claim about capability.
        "uncollectable_stub": sum(1 for r in reports if _is_uncollectable(r, "stub_fail")),
        "uncollectable_reference": sum(
            1 for r in reports if _is_uncollectable(r, "reference_solution")
        ),
        "duplicates": dupes,
        "tier_distribution": dict(sorted(by_tier.items())),
        "reports": [r.to_dict() for r in reports],
        "content_fingerprint": set_fingerprint(cases),
    }


def set_fingerprint(cases: list[Case]) -> str:
    """Content hash of a case set: stable across reordering, changes when any case changes."""
    parts = sorted(f"{c.case_id}:{case_fingerprint(c)}" for c in cases)
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()[:16]


def annotate_case_metadata(case_path: Path, report: CaseValidityReport) -> None:
    raw = load_json(case_path)
    meta = dict(raw.get("metadata") or {})
    meta["difficulty"] = report.difficulty
    meta["fingerprint"] = report.fingerprint
    meta["validity_ok"] = report.ok
    meta["validity_issues"] = [i.to_dict() for i in report.issues]
    # Without these the reason a case was rejected is lost the moment the audit run ends, and
    # "broken workspace" becomes indistinguishable from "hard" again on the next read.
    meta["uncollectable_stub"] = _is_uncollectable(report, "stub_fail")
    meta["uncollectable_reference"] = _is_uncollectable(report, "reference_solution")
    if report.tier:
        meta["tier"] = report.tier
    raw["metadata"] = meta
    write_json(case_path, raw)
