from __future__ import annotations

import json
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
        return _grade_llm_judge(case, workspace)
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


def _grade_llm_judge(case: Case, workspace: Path) -> GradeResult:
    """Score workspace against prompt/rubric via OpenAI-compatible chat."""
    from aibench.env_config import openai_settings
    import httpx

    settings = openai_settings()
    if not settings["api_key"] or not settings["base_url"] or not settings["model"]:
        return GradeResult(
            passed=False,
            mode="llm_judge",
            detail="llm_judge requires OPENAI_API_KEY/BASE_URL/MODEL",
            infra_error=True,
        )

    files_blob = []
    for p in sorted(workspace.rglob("*")):
        if not p.is_file() or p.stat().st_size > 100_000:
            continue
        try:
            rel = p.relative_to(workspace).as_posix()
            files_blob.append(f"### {rel}\n{p.read_text(encoding='utf-8', errors='replace')[:4000]}")
        except OSError:
            continue
    rubric = case.grader.judge_rubric or "Score whether the solution fulfills the user task."
    thr = case.grader.judge_threshold if case.grader.judge_threshold is not None else 0.7
    system = (
        "You are a strict coding benchmark judge. "
        'Reply ONLY JSON: {"score": 0.0-1.0, "passed": true|false, "reason": "..."}'
    )
    user = (
        f"Task:\n{case.prompt[:2000]}\n\nRubric:\n{rubric}\n\n"
        f"Workspace files:\n{chr(10).join(files_blob) or '(empty)'}\n\n"
        f"Threshold for pass: {thr}"
    )
    base = settings["base_url"].rstrip("/")
    try:
        with httpx.Client(timeout=90.0) as client:
            resp = client.post(
                f"{base}/chat/completions",
                headers={
                    "Authorization": f"Bearer {settings['api_key']}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": settings["model"],
                    "temperature": 0,
                    "max_tokens": 512,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                },
            )
            resp.raise_for_status()
            msg = resp.json()["choices"][0]["message"]
            content = (msg.get("content") or msg.get("reasoning_content") or "").strip()
    except Exception as e:  # noqa: BLE001
        return GradeResult(
            passed=False,
            mode="llm_judge",
            detail=f"llm_judge request failed: {e}",
            infra_error=True,
        )

    text = content
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        start, end = text.find("{"), text.rfind("}")
        data = json.loads(text[start : end + 1] if start >= 0 else text)
        score = float(data.get("score"))
        passed = bool(data.get("passed")) if "passed" in data else score >= float(thr)
        reason = str(data.get("reason") or "")
    except Exception as e:  # noqa: BLE001
        return GradeResult(
            passed=False,
            mode="llm_judge",
            detail=f"llm_judge parse fail: {e}; raw={content[:200]}",
        )
    return GradeResult(
        passed=passed,
        mode="llm_judge",
        score=score,
        detail=f"score={score} thr={thr} {reason}".strip(),
    )
