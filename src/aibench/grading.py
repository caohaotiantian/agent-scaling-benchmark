from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path

from aibench.languages import (
    case_language_is_javascript,
    pass_ratio,
    registered_spec,
    unsupported_node_reason,
)
from aibench.models import Case, GradeResult
from aibench.workspace import safe_relpath as _safe_relpath


def _normalize(text: str) -> str:
    lines = [ln.rstrip() for ln in text.replace("\r\n", "\n").split("\n")]
    return "\n".join(lines).strip() + "\n"


def check_protected_paths(case: Case, workspace: Path) -> str | None:
    """Return a violation detail if any protected path no longer matches ``context.files``.

    The expected bytes come from the case's own context files, so a case never has to
    carry hashes that could drift out of sync with the workspace it ships.
    """
    if not case.grader.protected_paths:
        return None
    # Normalised on both sides, the same way the workspace was built. A case path may be
    # absolute -- real traces carry `/home/someone/...` -- and `workspace / "/home/x"` resolves
    # to `/home/x`, which does not exist, so the file read as deleted and the run was reported
    # as tampering. Every other call site here already routes through safe_relpath; this one
    # did not, which is what its docstring says the helper exists to prevent.
    by_path = {_safe_relpath(fb.path): fb.content for fb in case.files}
    for declared in case.grader.protected_paths:
        rel = _safe_relpath(declared)
        if rel not in by_path:
            return f"protected_path_not_in_context: {declared}"
        target = workspace / rel
        if not target.is_file():
            return f"protected_path_deleted: {declared}"
        if target.read_text(encoding="utf-8", errors="replace") != by_path[rel]:
            return f"protected_path_modified: {declared}"
    return None


# Files that change how pytest collects or runs, rather than what the code does. A solver
# has no legitimate reason to introduce one in these self-contained cases, and each is a
# well-known way to make a suite pass without fixing anything.
_COLLECTION_CONTROL_FILES = frozenset(
    {
        "conftest.py",
        "pytest.ini",
        "setup.cfg",
        "tox.ini",
        "pyproject.toml",
        "sitecustomize.py",
        "usercustomize.py",
    }
)
#: Wall-clock ceiling on one grader invocation. Named so the timeout message can quote it.
_GRADER_TIMEOUT_S = 120

_SKIP_MARKERS = re.compile(
    # `unittest.skip\b` matched neither `skipIf` nor `skipUnless` — the two forms an agent
    # would actually reach for, because they take a condition and read as legitimate.
    r"@(?:pytest\.mark\.(?:skip|skipif|xfail)|unittest\.skip(?:If|Unless)?)\b|"
    r"\bpytest\.skip\s*\(|"
    r"\bpytest\.exit\s*\(|\braise\s+unittest\.SkipTest\b"
)


def workspace_inventory(workspace: Path) -> dict[str, str]:
    """Path -> content digest for everything in the workspace, as materialization left it.

    Taken *before* the agent runs, and handed back to :func:`grade_case`. It is what makes the
    interference check able to distinguish "the case shipped this" from "the submission added
    it": `context.files` is only the inline overlay, and `materialize_workspace` also lays down
    a snapshot, a git checkout and whatever `setup_commands` produced. Judging against
    `context.files` alone accused a submission of tampering for a `pytest.ini` that came with
    the project — and, because the audit gates grade through this same function, wrote
    `validity_ok: false` into the case on disk for it.
    """
    out: dict[str, str] = {}
    for path in workspace.rglob("*"):
        if path.is_symlink() or not path.is_file():
            continue
        try:
            out[path.relative_to(workspace).as_posix()] = hashlib.sha256(
                path.read_bytes()
            ).hexdigest()
        except OSError:
            continue
    return out


def detect_grading_interference(
    case: Case,
    workspace: Path,
    *,
    baseline: dict[str, str] | None = None,
) -> str | None:
    """Catch attempts to pass by changing how tests run rather than what the code does.

    ``protected_paths`` covers editing the visible tests. This covers the routes around them:
    dropping in a ``conftest.py`` that patches the module under test, adding a ``pytest.ini``
    whose addopts deselect the failing cases, or sprinkling skip markers into files the case
    never shipped.

    A *shipped* file used to be exempt outright, on the argument that ``protected_paths``
    already covered it. That is only true for the paths ``protect_visible_tests`` names, and it
    names ``role == "test"`` files only — so a ``conftest.py`` carrying ``role: impl`` was
    neither protected nor scanned, and the case set on disk contains exactly that shape. A
    shipped collection-control file is therefore exempt only while it still holds the bytes the
    case shipped. The single exception is a collection-control file that *is* the reference
    solution's target: the case cannot be solved without editing it, and
    :func:`aibench.validity.check_gold_is_not_collection_control` rejects that shape at audit
    time instead.

    ``baseline`` is :func:`workspace_inventory` taken after materialization and before the agent
    ran. Judging against ``context.files`` alone is wrong for any case whose workspace has a
    base layer — a snapshot, a git checkout, `setup_commands` — because none of those files are
    in ``context.files``, and a `pytest.ini` or a `@pytest.mark.skipif` that came with the
    project would then be reported as tampering by the submission that never touched it. A
    symlink is followed rather than skipped: `Path.is_file()` follows, and skipping it let a
    planted `conftest.py -> /tmp/evil.py` through the gate pytest imports it through.
    """
    shipped: dict[str, str] = {}
    for fb in case.files:
        try:
            shipped[_safe_relpath(fb.path)] = fb.content
        except ValueError:
            continue
    gold_targets: set[str] = set()
    for gf in case.grader.gold_files:
        try:
            gold_targets.add(_safe_relpath(gf.path))
        except ValueError:
            continue
    baseline = baseline or {}

    def _unchanged_since_materialization(rel: str, path: Path) -> bool:
        digest = baseline.get(rel)
        if digest is None:
            return False
        try:
            return hashlib.sha256(path.read_bytes()).hexdigest() == digest
        except OSError:
            return False

    for path in sorted(workspace.rglob("*")):
        # Symlinks are resolved, not skipped: `is_file()` follows one, and a link is the
        # cheapest way to put content pytest will import somewhere this loop does not read.
        if not path.is_file():
            continue
        rel = path.relative_to(workspace).as_posix()
        if path.name in _COLLECTION_CONTROL_FILES:
            if path.is_symlink():
                # Nothing legitimate needs one, and its target is outside what the case ships.
                return f"grading_interference_symlink: {rel}"
            if _unchanged_since_materialization(rel, path):
                continue
            if rel not in shipped:
                return f"grading_interference_file: {rel}"
            if rel in gold_targets:
                continue
            try:
                current = path.read_text(encoding="utf-8", errors="replace")
            except OSError as e:
                return f"grading_interference_unreadable: {rel} ({e})"
            if current != shipped[rel]:
                return f"grading_interference_file_modified: {rel}"
            continue
        if rel in shipped or _unchanged_since_materialization(rel, path):
            continue  # covered by protected_paths, or came with the workspace
        if path.suffix == ".py":
            try:
                body = path.read_text(encoding="utf-8", errors="replace")
            except OSError as e:
                return f"grading_interference_unreadable: {rel} ({e})"
            if _SKIP_MARKERS.search(body):
                return f"grading_interference_skip_marker: {rel}"
    return None


def inject_hidden_tests(case: Case, workspace: Path) -> list[str]:
    """Write the grader's hidden tests into the workspace. Call only after the agent stops.

    Anything already sitting at a hidden test's path is removed first. The names are
    predictable (``*_spec.py`` at the workspace root), so an agent can plant a symlink or a
    directory there: a symlink makes ``write_text`` follow it and write the hidden test
    *outside* the workspace, and a directory makes it raise ``IsADirectoryError`` out of
    ``grade_case``.
    """
    written: list[str] = []
    for fb in case.grader.hidden_tests:
        rel = _safe_relpath(fb.path)
        path = workspace / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.is_symlink() or path.is_file():
            path.unlink()
        elif path.is_dir():
            shutil.rmtree(path)
        path.write_text(fb.content, encoding="utf-8")
        written.append(rel)
    return written


def grade_case(
    case: Case,
    workspace: Path,
    *,
    baseline: dict[str, str] | None = None,
) -> GradeResult:
    """Grade one submission.

    ``baseline`` is :func:`workspace_inventory` taken right after materialization. Without
    it the interference check cannot tell a file the workspace was built with from one the
    submission added, so it is only armed for cases that declared `protected_paths` — the
    conservative behaviour, and the one every caller had before.
    """
    mode = case.grader.mode

    violation = check_protected_paths(case, workspace)
    arm_interference = bool(case.grader.protected_paths) or (
        baseline is not None and mode in {"script", "composite"}
    )
    if violation is None and arm_interference:
        # Armed for every case whose verdict comes from running a suite, not only for cases
        # that declared `protected_paths`. The old condition left the gate off for every case
        # with none — 90 `_raw2026` and 12 `_geninput` cases among them — which is precisely
        # where a planted `conftest.py` costs the most, because there is no other anti-tampering
        # check at all. Files the case shipped are still exempt (see the function's docstring),
        # so this cannot fail a case for its own contents.
        violation = detect_grading_interference(case, workspace, baseline=baseline)
    if violation:
        return GradeResult(
            passed=False,
            mode=mode,
            score=0.0,
            detail=violation,
            # A malformed case is our bug, not the agent's — keep it out of the success rate.
            infra_error=violation.startswith("protected_path_not_in_context"),
            reward_hack=not violation.startswith("protected_path_not_in_context"),
        )
    if mode in {"script", "composite"}:
        inject_hidden_tests(case, workspace)

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
            # Falling through on an uncollectable workspace lets the gold check pass a case
            # whose tests never ran, and drops the collection verdict on the floor.
            if r.passed or r.infra_error or r.collection_error:
                return r
        if case.grader.gold_files or case.grader.key_lines:
            return _grade_gold(case, workspace)
        return GradeResult(passed=False, mode=mode, detail="composite has no sub-graders")
    return GradeResult(passed=False, mode=mode, detail=f"unknown grader mode: {mode}")


def _grader_env() -> dict[str, str]:
    """The environment a grader runs under, with the sources of run-to-run drift pinned.

    The grader inherited the caller's environment whole. Three of those variables decide
    whether the same code produces the same verdict:

    * ``PYTHONHASHSEED`` — set randomly per interpreter, so any test that iterates a set or a
      dict built from one and asserts an order passes or fails by luck. Pinned to 0.
    * ``PYTHONDONTWRITEBYTECODE`` — a workspace is thrown away after grading, and writing
      ``__pycache__`` into it makes the post-grading inventory differ from the pre-grading one
      for reasons the submission had nothing to do with.
    * ``PYTHONWARNINGS`` — a deprecation warning on stderr is not a failure, and leaving the
      caller's ``error`` setting in place turns it into one.
    """
    import os

    env = dict(os.environ)
    env["PYTHONHASHSEED"] = "0"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env.pop("PYTHONWARNINGS", None)
    return env


def _grade_script(case: Case, workspace: Path) -> GradeResult:
    cmd = case.grader.command
    if not cmd:
        return GradeResult(passed=False, mode="script", detail="missing grader.command")
    if case_language_is_javascript(case.language) and (reason := unsupported_node_reason()):
        # An unusable runner is a harness failure, not a verdict about the submission. Left
        # unchecked it is worse than a crash: `node --test` below 22.18 exits 0 having
        # discovered nothing, and 0 is a pass.
        return GradeResult(
            passed=False,
            mode="script",
            detail=f"javascript grading unavailable: {reason}",
            infra_error=True,
        )
    try:
        proc = subprocess.run(
            cmd,
            shell=True,
            cwd=workspace,
            capture_output=True,
            text=True,
            timeout=_GRADER_TIMEOUT_S,
            check=False,
            env=_grader_env(),
        )
    except subprocess.TimeoutExpired:
        # Kept as `infra_error` — the harness cannot tell an agent-authored hang from a slow
        # machine, and charging the model for the second is worse than excusing it the first.
        # But the two are not the same event and the artifact now says which one this was, so
        # the classification is auditable rather than assumed: `grader timeout` appears 0 times
        # across the 218 `results.jsonl` files on disk, against 1,454 agent-side infra errors,
        # which is the evidence that this branch is latent rather than a live distortion.
        return GradeResult(
            passed=False,
            mode="script",
            detail=f"grader timeout after {_GRADER_TIMEOUT_S}s: {cmd}",
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
    combined = f"{proc.stdout or ''}\n{proc.stderr or ''}"
    tail = (proc.stdout or "")[-1500:] + (proc.stderr or "")[-1500:]
    # Only a registered runner's exit codes and tally can be read; for anything else the
    # harness has no idea what the output means and must not guess it is broken.
    # Judge only output this harness knows how to read: the declared language must have a
    # runner here, and the grader command must actually be driven by it. `python check.py` is
    # an accepted grader command and prints no tally, so reading it as pytest would call every
    # genuine assertion failure a broken workspace.
    spec = registered_spec(case.language)
    uncollectable = bool(
        spec and not ok and spec.drives(cmd) and spec.is_uncollectable(proc.returncode, combined)
    )
    return GradeResult(
        passed=ok,
        mode="script",
        score=1.0 if ok else 0.0,
        detail=f"exit={proc.returncode}\n{tail}".strip(),
        # A suite that never ran earned no partial credit; 0.0 would read as "everything failed".
        test_pass_ratio=None if uncollectable else pass_ratio(combined, language=case.language),
        collection_error=uncollectable,
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
                # `errors="replace"`, like every other read in this module: a non-UTF-8 byte the
                # agent wrote to a gold path used to raise out of `grade_case` and take the
                # whole run's results with it.
                blobs.append(p.read_text(encoding="utf-8", errors="replace"))
        if not blobs:
            # Hidden tests were injected into this workspace; scanning them would let a
            # key_line match against the specification instead of against the solution.
            injected = {workspace / _safe_relpath(fb.path) for fb in g.hidden_tests}
            for p in workspace.rglob("*"):
                if p in injected:
                    continue
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
        actual = p.read_text(encoding="utf-8", errors="replace")
        expected = gold.content
        if g.match == "exact":
            if actual != expected:
                mismatches.append(f"exact mismatch: {gold.path}")
        elif _normalize(actual) != _normalize(expected) and not _soft_equal(actual, expected):
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
    import httpx

    from aibench.env_config import openai_settings

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
            files_blob.append(
                f"### {rel}\n{p.read_text(encoding='utf-8', errors='replace')[:4000]}"
            )
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
    from aibench.retry import retry_call

    try:

        def _judge_req() -> str:
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
                text = (msg.get("content") or msg.get("reasoning_content") or "").strip()
                if not text:
                    raise ValueError("empty content in response")
                return text

        content = retry_call(_judge_req, label="llm_judge")
    except Exception as e:
        return GradeResult(
            passed=False,
            mode="llm_judge",
            detail=f"llm_judge request failed after retries: {e}",
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
    except Exception as e:
        return GradeResult(
            passed=False,
            mode="llm_judge",
            detail=f"llm_judge parse fail: {e}; raw={content[:200]}",
        )
    return GradeResult(
        passed=passed,
        mode="llm_judge",
        score=score,
        # The judge model is taken from the environment and used to be recorded nowhere, so a
        # judged result could not say what judged it — the same hole `code_version` had, on the
        # grading side. Every other adapter resolves its model config-first; this one cannot,
        # so naming it in the detail is the least that keeps the verdict attributable.
        detail=f"judge={settings['model']} score={score} thr={thr} {reason}".strip(),
    )
