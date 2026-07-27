from __future__ import annotations

import re
import subprocess
from pathlib import Path

from aibench.models import Case, GradeResult


def _normalize(text: str) -> str:
    lines = [ln.rstrip() for ln in text.replace("\r\n", "\n").split("\n")]
    return "\n".join(lines).strip() + "\n"


def grade_case(case: Case, workspace: Path) -> GradeResult:
    mode = case.grader.mode
    if mode == "script":
        return _grade_script(case, workspace)
    if mode == "gold":
        return _grade_gold(case, workspace)
    if mode == "llm_judge":
        return GradeResult(
            passed=False,
            mode=mode,
            score=None,
            detail="llm_judge not enabled in this run (stub)",
        )
    if mode == "composite":
        # Prefer script if present, else gold.
        if case.grader.command:
            r = _grade_script(case, workspace)
            if r.passed or r.infra_error:
                return r
        if case.grader.gold_files or case.grader.key_lines:
            return _grade_gold(case, workspace)
        return GradeResult(passed=False, mode=mode, detail="composite has no sub-graders")
    return GradeResult(passed=False, mode=mode, detail=f"unknown grader mode: {mode}")


def _grade_script(case: Case, workspace: Path) -> GradeResult:
    cmd = case.grader.command
    if not cmd:
        return GradeResult(passed=False, mode="script", detail="missing grader.command")
    try:
        proc = subprocess.run(
            cmd,
            shell=True,
            cwd=workspace,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return GradeResult(
            passed=False,
            mode="script",
            detail="grader timeout",
            infra_error=True,
        )
    except OSError as e:
        return GradeResult(
            passed=False,
            mode="script",
            detail=f"grader spawn failed: {e}",
            infra_error=True,
        )
    ok = proc.returncode == 0
    tail = (proc.stdout or "")[-1500:] + (proc.stderr or "")[-1500:]
    return GradeResult(
        passed=ok,
        mode="script",
        score=1.0 if ok else 0.0,
        detail=f"exit={proc.returncode}\n{tail}".strip(),
    )


def _grade_gold(case: Case, workspace: Path) -> GradeResult:
    g = case.grader
    if g.key_lines and g.match == "contains_key_lines":
        # Prefer gold paths if present on disk; otherwise scan whole workspace.
        blobs: list[str] = []
        targets = [gf.path for gf in g.gold_files] if g.gold_files else []
        for rel in targets:
            p = workspace / rel
            if p.is_file():
                blobs.append(p.read_text(encoding="utf-8"))
        if not blobs:
            for p in workspace.rglob("*"):
                if p.is_file() and p.stat().st_size < 2_000_000:
                    try:
                        blobs.append(p.read_text(encoding="utf-8", errors="replace"))
                    except OSError:
                        continue
        joined = "\n".join(blobs)
        missing = [k for k in g.key_lines if k not in joined]
        ok = not missing
        return GradeResult(
            passed=ok,
            mode="gold",
            score=1.0 if ok else 0.0,
            detail="ok" if ok else f"missing key_lines: {missing}",
        )

    if not g.gold_files:
        return GradeResult(passed=False, mode="gold", detail="no gold_files")

    mismatches: list[str] = []
    for gold in g.gold_files:
        p = workspace / gold.path
        if not p.is_file():
            mismatches.append(f"missing file {gold.path}")
            continue
        actual = p.read_text(encoding="utf-8")
        expected = gold.content
        if g.match == "exact":
            if actual != expected:
                mismatches.append(f"exact mismatch: {gold.path}")
        else:
            if _normalize(actual) != _normalize(expected):
                # Soft: if key logic present via stripping comments-only drift
                if not _soft_equal(actual, expected):
                    mismatches.append(f"normalized mismatch: {gold.path}")

    ok = not mismatches
    return GradeResult(
        passed=ok,
        mode="gold",
        score=1.0 if ok else 0.0,
        detail="ok" if ok else "; ".join(mismatches),
    )


def _soft_equal(a: str, b: str) -> bool:
    def collapse(s: str) -> str:
        s = re.sub(r"#.*", "", s)
        s = re.sub(r"\s+", "", s)
        return s

    return collapse(a) == collapse(b)
