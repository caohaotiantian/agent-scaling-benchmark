"""Scientific validity gates for benchmark cases."""

from __future__ import annotations

import hashlib
import json
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
FINGERPRINT_VERSION = "v3"


def _file_digests(entries: Any) -> list[list[str]]:
    """``[path, sha256(content)]`` per file, in declaration order.

    Order is preserved because the agent prompt lists files in it (``openai_compat`` and
    ``tool_loop`` both join ``case.files`` as given), so a reordering is a different prompt.
    """
    out: list[list[str]] = []
    for f in entries or []:
        if isinstance(f, dict):
            path, content = str(f.get("path") or ""), str(f.get("content") or "")
        else:
            path, content = str(getattr(f, "path", "")), str(getattr(f, "content", "") or "")
        out.append([path, hashlib.sha256(content.encode("utf-8")).hexdigest()])
    return out


def _grader_basis(grader: Any) -> dict[str, Any]:
    """The grader's decision surface — everything that changes what counts as passing.

    A raw dict is normalised through :class:`GraderSpec` first so that the dict and ``Case``
    forms of the same case cannot disagree over a defaulted field.
    """
    if isinstance(grader, dict):
        from aibench.models import GraderSpec

        grader = GraderSpec.from_dict({**grader, "mode": grader.get("mode") or ""})
    return {
        "mode": str(grader.mode or ""),
        "command": str(grader.command or ""),
        "match": str(grader.match or ""),
        "key_lines": list(grader.key_lines or []),
        "protected_paths": sorted(str(p) for p in (grader.protected_paths or [])),
        "judge_rubric": str(grader.judge_rubric or ""),
        "judge_threshold": grader.judge_threshold,
        "gold_files": _file_digests(grader.gold_files),
        "hidden_tests": _file_digests(grader.hidden_tests),
    }


def _workspace_basis(case: Case | dict[str, Any]) -> dict[str, Any]:
    """The workspace spec, when the case builds its workspace from something external.

    Only the spec is hashed, not the snapshot or clone it names — this function does no I/O
    and has no case-set directory to resolve against. ``external_workspace`` marks the case so
    the reuse gate can refuse it outright rather than trust a fingerprint that cannot witness
    a change to the code the agent actually works on.
    """
    from aibench.workspace import WorkspaceSpec

    ws = case.workspace if isinstance(case, Case) else None
    if ws is None:
        raw = {} if isinstance(case, Case) else ((case.get("context") or {}).get("workspace") or {})
        ws = WorkspaceSpec.from_dict(raw)
    spec = {k: getattr(ws, k) for k in vars(ws)}
    return {
        "mode": str(spec.get("mode") or "inline"),
        "spec": json.dumps(spec, sort_keys=True, default=str),
    }


def external_workspace(case: Case | dict[str, Any]) -> bool:
    """True when the case's code comes from a snapshot or clone rather than inline files."""
    return _workspace_basis(case)["mode"] not in {"", "inline"}


def case_fingerprint(case: Case | dict[str, Any]) -> str:
    """Identify a case by everything that decides whether a submission passes.

    Hashing only the prompt and the file *paths* meant a case whose stub and reference
    solution had been replaced wholesale kept its identity, so ``calibrate-cases
    --reuse-from`` returned the previous p_hat for code it had never run. Hashing the file
    contents alone left the same hole open through the grader: swapping ``grader.command``
    for one that always fails did not move the fingerprint either.

    Not covered: the *contents* of a snapshot or git workspace — see :func:`external_workspace`,
    which the reuse gate consults instead.
    """
    if isinstance(case, Case):
        prompt, task_type, language = case.prompt, case.task_type, case.language
        files, grader = case.files, case.grader
    else:
        prompt = str(case.get("prompt") or "")
        task_type = str(case.get("task_type") or "")
        language = str(case.get("language") or "")
        files = (case.get("context") or {}).get("files") or []
        grader = case.get("grader") or {}
    basis = json.dumps(
        {
            "version": FINGERPRINT_VERSION,
            "task_type": task_type,
            "language": language,
            "prompt": prompt.strip(),
            "files": _file_digests(files),
            "grader": _grader_basis(grader),
            "workspace": _workspace_basis(case),
        },
        sort_keys=True,
        ensure_ascii=False,
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


#: Reflection that hands a test the source of the thing it is meant to exercise.
_GETSOURCE = re.compile(r"\binspect\.getsource\b|\bgetsourcelines\b")

#: Where a read begins. Arguments are then taken by matching parentheses rather than by regex:
#: ``[^)]*`` stops at the first ``)``, so ``open(os.path.join(d, "o.txt"), "w")`` loses its mode
#: argument and reads as a read — and `os.path.join` in a test is entirely ordinary.
_READ_NAME = re.compile(r"\b(?:readFileSync|readFile|read_text|read_bytes|open)\s*\(")

#: A mode argument, matched against one argument at a time. Scanning the whole argument list
#: for a quoted single letter meant `readFileSync(p, 'utf8').includes('a')` counted as a write
#: and skipped the read entirely: changing `'foo'` to `'a'` in an assertion was enough to walk
#: through an error-level gate.
_WRITE_MODE_ARG = re.compile(r"^(?:mode\s*=\s*)?['\"][wax]b?\+?['\"]$")

#: Asserting on text rather than on behaviour.
#:
#: Deliberately free of name-based alternatives. `\b(?:source|src|code)\s*\.` and a bare
#: `\.match\s*\(` matched `err.code.startsWith(...)`, `MAC_RE.match(mac)`, a fixture variable
#: called `source`, and prose in a docstring. Measured over the two calibration sets they
#: contributed no discrimination at all — `_revmixed` stays at 12 and `_rev6` at 30 without
#: them — while costing three false positives on `_scaleprobe`.
_TEXT_ASSERT = re.compile(
    r"\.includes\s*\(|assert\.match\s*\(|toContain\s*\("
    r"|assertIn\s*\(|\bin\s+(?:source|src|content|code)\b"
)


def _call_args(text: str, open_paren: int) -> tuple[str, int]:
    """Argument text between matching parentheses, and the index just past the closer."""
    depth, i, quote = 0, open_paren, None
    while i < len(text):
        ch = text[i]
        if quote:
            if ch == "\\":
                i += 2
                continue
            if ch == quote:
                quote = None
        elif ch in "\"'`":
            quote = ch
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return text[open_paren + 1 : i], i + 1
        i += 1
    return text[open_paren + 1 :], len(text)


def _split_args(args: str) -> list[str]:
    """Top-level comma-separated arguments, ignoring commas nested in calls or strings."""
    out, depth, quote, start = [], 0, None, 0
    for i, ch in enumerate(args):
        if quote:
            if ch == "\\":
                continue
            if ch == quote:
                quote = None
        elif ch in "\"'`":
            quote = ch
        elif ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        elif ch == "," and depth == 0:
            out.append(args[start:i].strip())
            start = i + 1
    out.append(args[start:].strip())
    return [a for a in out if a]


def _reads(line: str) -> list[str]:
    """Argument lists of the read calls on one line; write-mode calls are not reads."""
    found: list[str] = []
    pos = 0
    while (m := _READ_NAME.search(line, pos)) is not None:
        args, end = _call_args(line, m.end() - 1)
        pos = end
        parts = _split_args(args)
        if any(_WRITE_MODE_ARG.match(p) for p in parts):
            continue
        found.append(args)
    return found


def _test_blob(case: Case) -> str:
    """Everything the solver is graded by: visible tests plus the hidden ones."""
    return "\n".join(
        [fb.content or "" for fb in case.files if fb.role == "test"]
        + [fb.content or "" for fb in case.grader.hidden_tests]
    )


def check_test_reads_source(case: Case) -> list[ValidityIssue]:
    """Reject tests that grep the implementation's text instead of running it.

    Reverse construction rests on the argument in :mod:`aibench.extract.reverse_case`: a model
    that writes tests which do not separate pre from post produces a case the existing gates
    reject, so it cannot make the task easier. That argument has a hole — *source text* always
    separates the two versions, because the fix changed the text. Such a suite passes
    :func:`check_stub_fails` and :func:`check_reference_solution` by construction, and grades
    transcription rather than behaviour.

    Measured on the 31 cases of ``_revmixed``: 12 hit, split JavaScript 11/14 against Python
    1/17. A single loose pattern ("a test that mentions ``readFileSync`` or ``open``") also
    caught six clean suites — three writing a fixture with ``open(..., 'w')``, two with
    ``os.fdopen(fd, 'w')`` and one calling ``urlopen`` — so the check is the union of three
    narrower rules, none of which is redundant:

    ===============================================  ======  ==============
    rule                                             hits    unique to it
    ===============================================  ======  ==============
    reflection (``inspect.getsource``)                    1               1
    read whose *arguments* name a shipped impl file       4               1
    read anywhere plus a substring/regex assertion       10               7
    ===============================================  ======  ==============

    The second rule is scoped to the *line* holding the read, deliberately. Matching the whole
    file turns it into "any read at all", because a JavaScript test necessarily names the
    implementation in its import line — 4 hits become 11 on the same set, and legitimate data
    fixtures start being rejected. A line is narrow enough to exclude the import and wide enough
    to catch ``Path('impl.py').read_text()``, where the name precedes the call.

    Known limitation: when the behaviour under test *is* file content — a documentation
    generator, a config writer — the third rule fires on a suite that is doing the right thing.
    Two such cases exist in ``_scaleprobe``. Distinguishing them needs the read target resolved
    against the shipped files, which this does not attempt. The three published sets
    (``auto-v0``, ``disc-v0``, ``retrieval-v0``) take zero hits, but note that between them they
    contain only one read call at all, so that is weak evidence of precision.
    """
    blob = _test_blob(case)
    if not blob.strip():
        return []
    impls = {fb.path.rsplit("/", 1)[-1] for fb in case.files if fb.role == "impl"}

    reasons: list[str] = []
    if _GETSOURCE.search(blob):
        reasons.append("reads its own source via inspect")

    reads_impl_by_name = False
    reads_anything = False
    for line in blob.splitlines():
        found = _reads(line)
        if not found:
            continue
        reads_anything = True
        named = sorted(n for n in impls if re.search(rf"(?<![\w.]){re.escape(n)}(?![\w])", line))
        if named:
            reads_impl_by_name = True
            reasons.append(f"reads the implementation by name: {', '.join(named)}")

    if reads_anything and not reads_impl_by_name and _TEXT_ASSERT.search(blob):
        reasons.append("reads a file and asserts on its text")

    if not reasons:
        return []
    return [
        ValidityIssue(
            "test_reads_source_text",
            "error",
            "grading is transcription, not behaviour: " + "; ".join(dict.fromkeys(reasons)),
        )
    ]


#: Detail prefixes the audit keys off. Defined once so the reported reason and the boolean
#: derived from it cannot drift apart.
STUB_UNCOLLECTABLE = "stub_uncollectable"
REFERENCE_UNCOLLECTABLE = "reference_solution_uncollectable"


def check_stub_fails(
    case: Case,
    *,
    case_set: str | None = None,
    reference_collects: bool | None = None,
    stub_is_complete: bool = False,
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

    ``stub_is_complete`` says the stub is a whole, working file rather than a hollowed-out
    one, which is what reverse construction produces: both sides are real versions of the same
    file. There "uncollectable" can never be the legitimate "implement this" shape, and
    ``reference_collects is True`` stops being evidence that the stub's workspace is sound —
    it only proves the *gold's* dependencies resolve. Measured on 22 reverse-constructed
    cases: 5 of the 6 that passed this gate did so because the pre-edit file imported numpy,
    pandas or torch and the post-edit file did not. The tests separated the two versions by
    which packages were installed, not by the defect, and three of those cases then failed to
    collect on 8 of 9 calibration attempts.
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
        if grade.collection_error and (stub_is_complete or reference_collects is False):
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
    issues.extend(check_test_reads_source(case))

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

    # Reverse construction ships the file as the trace found it, so the stub is complete and
    # an uncollectable one is always a broken workspace rather than an unimplemented function.
    stub_is_complete = str((case.metadata or {}).get("generation") or "") == "reverse"
    stub_ok, stub_detail = check_stub_fails(
        case,
        case_set=case_set,
        reference_collects=reference_collects,
        stub_is_complete=stub_is_complete,
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
