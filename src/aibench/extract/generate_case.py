"""Generate schema-shaped benchmark cases from drafts/sessions (heuristic + LLM)."""

from __future__ import annotations

import json
import re
from typing import Any

import httpx

from aibench.env_config import openai_settings
from aibench.extract.history_parse import guess_language, guess_task_type
from aibench.extract.sessions import redact_secrets, task_fingerprint
from aibench.extract.tier_shaping import settle_tier
from aibench.tiers import find_disclosures, tier_spec

_SAFE_GRADER_CMD = re.compile(
    r"^(python(\d+(\.\d+)?)?\s+-m\s+pytest\b|python(\d+(\.\d+)?)?\s+\S+\.py\b|true\b)",
    re.I,
)


def is_safe_grader_command(cmd: str | None) -> bool:
    if not cmd:
        return True
    c = cmd.strip()
    if len(c) > 200 or "&&" in c or ";" in c or "|" in c or "`" in c:
        return False
    return bool(_SAFE_GRADER_CMD.match(c))


def heuristic_case_from_draft(draft: dict[str, Any], *, tier: str | None = None) -> dict[str, Any]:
    """Normalize a draft into a runnable-ish case; prefer gold key_lines or simple script."""
    case = json.loads(json.dumps(draft))  # deep copy
    case["prompt"] = redact_secrets(case.get("prompt") or "")
    ctx = case.setdefault("context", {})
    files = ctx.get("files") or []
    cleaned = []
    for f in files:
        cleaned.append(
            {
                "path": f["path"],
                "content": redact_secrets(f.get("content") or ""),
            }
        )
    if not cleaned:
        lang = case.get("language") or "python"
        path = {"python": "main.py", "javascript": "main.js"}.get(lang, "main.txt")
        cleaned = [{"path": path, "content": "# TODO workspace\n"}]
    ctx["files"] = cleaned

    grader = case.get("grader") or {"mode": "gold"}
    if grader.get("mode") == "script" and not is_safe_grader_command(grader.get("command")):
        grader = {"mode": "gold", "match": "contains_key_lines", "key_lines": ["def "]}

    # Gold key lines must come from assistant solution code, not from prompt prose / trees.
    # Context files are the *starting* workspace; grader checks agent output contains solution keys.
    if grader.get("mode") == "gold":
        gold = grader.get("gold_files") or []
        gold_body = (gold[0].get("content") if gold else "") or ""
        keys = [k for k in _default_key_lines(gold_body) if _is_useful_key_line(k)]
        # Drop keys already present in starting context (would pass without agent work).
        ctx_blob = "\n".join(f.get("content") or "" for f in cleaned)
        keys = [k for k in keys if k not in ctx_blob]
        if not keys:
            # No separable solution signal — mark weak and use a soft structural check
            keys = ["def "] if any(f["path"].endswith(".py") for f in cleaned) else ["function "]
            meta_weak = True
        else:
            meta_weak = False
        grader = {
            "mode": "gold",
            "match": "contains_key_lines",
            "key_lines": keys[:5],
            "gold_files": gold[:1] if gold else [],
        }
    else:
        meta_weak = grader.get("mode") != "script"
    case["grader"] = grader
    meta = case.setdefault("metadata", {})
    meta["review_status"] = meta.get("review_status") or "needs_review"
    meta["split"] = meta.get("split") or "auto"
    meta["generation"] = "heuristic"
    meta["weak_grader"] = (
        bool(meta_weak) if grader.get("mode") == "gold" else grader.get("mode") != "script"
    )
    case["schema_version"] = case.get("schema_version") or "0.1"
    if not case.get("task_type"):
        case["task_type"] = guess_task_type(case["prompt"])
    if not case.get("language"):
        case["language"] = guess_language(case["prompt"], cleaned, [])
    if not case.get("case_id"):
        fp = task_fingerprint(case["prompt"], [f["path"] for f in cleaned])
        case["case_id"] = f"auto-{fp}"

    requested = tier or draft_tier(draft, default="T1")
    settled, notes = settle_tier(case, requested)
    # settle_tier replaces the case contents, so re-read metadata rather than reusing `meta`.
    settled_meta = case.setdefault("metadata", {})
    settled_meta["tier_requested"] = requested
    settled_meta["tier_notes"] = notes
    if not settled:
        settled_meta.pop("capability_axes", None)
    return case


def _is_useful_key_line(s: str) -> bool:
    if not s or len(s) < 4:
        return False
    # drop markdown trees, absolute paths, ascii art
    if s.startswith(("├", "│", "└", "┌", "─", "/Users/", "C:\\", "open ")):
        return False
    return not ("python-algorithms/" in s or s in {"↓", "{", "}"})


def _default_key_lines(content: str) -> list[str]:
    lines = []
    for ln in content.splitlines():
        s = ln.strip()
        if not _is_useful_key_line(s):
            continue
        if any(
            k in s for k in ("def ", "class ", "function ", "return ", "import ", "public ", "fn ")
        ):
            lines.append(s[:100])
        if len(lines) >= 3:
            break
    return lines


_TASK_TYPE_MAP = {
    "implement": "feature",
    "implementation": "feature",
    "fix": "bugfix",
    "bug": "bugfix",
    "bug_fix": "bugfix",
    "refactor": "refactor",
    "test": "test_gen",
    "tests": "test_gen",
    "explain": "explain_to_edit",
}


def _extract_json_object(text: str) -> dict[str, Any]:
    raw = (text or "").strip()
    if not raw:
        raise ValueError("empty LLM content")
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        start = raw.find("{")
        end = raw.rfind("}")
        if start < 0 or end <= start:
            raise ValueError(f"no JSON object in LLM content: {raw[:200]!r}") from None
        data = json.loads(raw[start : end + 1])
    if not isinstance(data, dict):
        raise ValueError("LLM JSON root must be object")
    return data


def _coerce_scalar_fields(data: dict[str, Any]) -> None:
    """Normalise the scalar fields generators get the JSON type wrong on.

    `"schema_version": 0.1` arrives as a number often enough that rejecting the case over it
    cost 9 of 69 in one batch — a version field's quoting says nothing about case quality.
    """
    data["task_type"] = _normalize_task_type(data.get("task_type"))
    data["language"] = str(data.get("language") or "python")
    data["schema_version"] = str(data.get("schema_version") or "0.1")
    data["case_id"] = str(data.get("case_id") or "")


def _normalize_task_type(value: Any) -> str:
    s = str(value or "feature").strip().lower().replace("-", "_").replace(" ", "_")
    if s in {"bugfix", "feature", "refactor", "explain_to_edit", "test_gen", "pairwise"}:
        return s
    return _TASK_TYPE_MAP.get(s, "feature")


# What each tier asks the generator to produce. The structural guarantees are enforced
# afterwards by tier_shaping.settle_tier — these briefs only raise the hit rate.
_TIER_BRIEFS: dict[str, str] = {
    "T1": (
        "TIER T1 (floor anchor — every model should solve this):\n"
        "- One implementation file with a single localized defect, marked `# BUG: ...`.\n"
        "- The prompt may name the defective mechanism directly.\n"
        "- One visible pytest file, 2-4 test functions."
    ),
    "T2": (
        "TIER T2 (the solver must locate the defect itself):\n"
        "- One or two implementation files with a single defect.\n"
        "- NO `# BUG` / `# FIXME` / `# TODO` comments anywhere. Do not mark the broken line.\n"
        "- The prompt describes ONLY the observable symptom, the way a user or a CI log would\n"
        "  report it (what was expected, what happened). It must NOT name the mechanism\n"
        "  ('inverted comparison', 'wrong offset', 'uses X instead of Y'), a line number, or\n"
        "  the fix. Writing 'the tests fail' is fine; writing why they fail is not.\n"
        "- One visible pytest file, 3-5 test functions."
    ),
    "T3": (
        "TIER T3 (hidden specification):\n"
        "- Everything T2 requires, plus:\n"
        "- The visible pytest file has AT LEAST 5 test functions and must cover boundary and\n"
        "  error cases, not just the happy path. Some of them will be hidden from the solver at\n"
        "  evaluation time, so a fix that only satisfies the first test must fail the rest.\n"
        "- `grader.gold_files` MUST contain the corrected full content of every implementation\n"
        "  file the fix touches. Include only files that actually change."
    ),
    "T4": (
        "TIER T4 (multi-file retrieval):\n"
        "- Everything T3 requires, plus:\n"
        "- A small package of AT LEAST 5 files. The defect spans AT LEAST 2 implementation\n"
        "  files (e.g. a helper returns the wrong shape and its caller compensates wrongly);\n"
        "  fixing only one of them must leave tests failing.\n"
        '- At least one plausible but irrelevant file marked `"role": "distractor"`.\n'
        "- `grader.gold_files` covers BOTH changed implementation files.\n"
        "- The visible pytest file has at least 6 test functions."
    ),
    "T5": (
        "TIER T5 (iterative self-repair):\n"
        "- Everything T4 requires, plus:\n"
        "- The correct behaviour cannot be derived by reading alone: it depends on runtime\n"
        "  state (ordering, accumulation, time windows, caching) that the solver has to observe\n"
        "  by running the tests and reacting to the failure.\n"
        "- The visible pytest file has at least 7 test functions."
    ),
}

_ROLE_HINT = (
    'context.files entries may carry "role": "impl" | "test" | "distractor" | "spec".\n'
    'Mark the pytest file as "test" and irrelevant files as "distractor".'
)


def _system_prompt_for_tier(tier: str) -> str:
    return (
        "You create self-contained Python coding benchmark cases that discriminate between "
        "coding agents of different capability.\n"
        "Return ONLY one JSON object (markdown fences allowed).\n"
        "Required keys: case_id, schema_version, task_type, language, prompt, context, grader, "
        "metadata.\n"
        "task_type MUST be one of: bugfix, feature, refactor, explain_to_edit, test_gen, pairwise.\n"
        "language should be python.\n"
        "context.files: array of {path, content, role}. Include a stub that is incomplete or "
        "wrong, and a pytest file that fails on the stub and passes on a correct fix.\n"
        f"{_ROLE_HINT}\n"
        'grader: {"mode":"script","command":"python -m pytest -q"}\n'
        "Keep each file under 120 lines. No secrets. No private paths.\n\n"
        f"{_TIER_BRIEFS[tier]}"
    )


def _delocalize_prompt(prompt: str, *, chat: Any) -> str:
    """Ask once for a symptom-only rewrite when the generated prompt gave the defect away."""
    rewritten = chat(
        [
            {
                "role": "system",
                "content": (
                    "Rewrite a coding task description so it reports only the OBSERVABLE SYMPTOM "
                    "— what was expected and what happened — with no mention of the cause, the "
                    "mechanism, the location, or the fix. Keep the same function and file names. "
                    "Reply with the rewritten description only, no preamble."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        512,
    )
    cleaned = re.sub(r"^```\w*\s*|\s*```$", "", rewritten.strip())
    return cleaned or prompt


def draft_tier(draft: dict[str, Any], *, default: str = "T2") -> str:
    """Tier suggested for this draft by its trace, falling back to ``default``."""
    tier = str((draft.get("metadata") or {}).get("tier") or "")
    return tier if tier in _TIER_BRIEFS else default


def _generate_timeout_s() -> float:
    """Per-request timeout for case generation.

    A reasoning model emitting a whole case as JSON regularly needs well over two minutes on a
    shared gateway, and a timeout only buys a retry of the same slow call. Override with
    AIBENCH_GENERATE_TIMEOUT.
    """
    import os

    try:
        return max(30.0, float(os.environ.get("AIBENCH_GENERATE_TIMEOUT", "300")))
    except ValueError:
        return 300.0


def generate_case_with_llm(
    draft: dict[str, Any],
    *,
    timeout_s: float | None = None,
    tier: str | None = None,
) -> dict[str, Any]:
    """Ask an LLM for a case shaped for ``tier``, then enforce that tier's invariants.

    The returned case is labelled with the highest tier it actually satisfies, which may be
    lower than the one requested.
    """
    settings = openai_settings()
    if not settings["api_key"] or not settings["base_url"] or not settings["model"]:
        raise RuntimeError(
            "OPENAI_API_KEY / OPENAI_BASE_URL / OPENAI_MODEL required for LLM generate"
        )

    timeout_s = timeout_s if timeout_s is not None else _generate_timeout_s()
    target_tier = tier or draft_tier(draft)
    tier_spec(target_tier)  # reject unknown tiers before spending a call
    prompt = draft.get("prompt") or ""
    files = ((draft.get("context") or {}).get("files")) or []
    file_summaries = []
    for f in files[:3]:
        body = (f.get("content") or "")[:600]
        file_summaries.append(f"### {f.get('path')}\n{body}")

    system = _system_prompt_for_tier(target_tier)
    user = (
        f"Build a self-contained coding task inspired by this real user request "
        f"(do NOT require the original repo):\n{prompt[:800]}\n\n"
        f"Optional context snippets:\n{chr(10).join(file_summaries) or '(none)'}\n\n"
        "Output the case JSON now."
    )
    base = settings["base_url"].rstrip("/")

    from aibench.retry import retry_call

    def _chat(messages: list[dict[str, str]], max_tokens: int = 8192) -> str:
        def _once() -> str:
            payload = {
                "model": settings["model"],
                "temperature": 0,
                "max_tokens": max_tokens,
                "messages": messages,
            }
            with httpx.Client(timeout=timeout_s) as client:
                resp = client.post(
                    f"{base}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {settings['api_key']}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                )
                resp.raise_for_status()
                body = resp.json()
                msg = body["choices"][0]["message"]
                content = msg.get("content") or ""
                reasoning = msg.get("reasoning_content") or ""
                # Prefer answer content; some models only fill reasoning and hit length.
                text = content.strip() if str(content).strip() else str(reasoning).strip()
                if not text:
                    raise ValueError(f"empty content in response: {str(body)[:300]}")
                return text

        return retry_call(_once, label="llm_generate_chat")

    try:
        raw_text = _chat(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            max_tokens=8192,
        )
        data = _extract_json_object(raw_text)
    except Exception:
        # Ultra-short path after retries exhausted on long prompt
        raw_text = _chat(
            [
                {
                    "role": "user",
                    "content": (
                        "Output ONLY a JSON coding benchmark case. No analysis.\n"
                        "Schema: case_id, schema_version=0.1, task_type=bugfix, language=python,\n"
                        "prompt, context.files=[{path,content,role}], "
                        "grader={mode:script,command,gold_files},\n"
                        "metadata={}.\n"
                        "Include a broken stub .py plus test_*.py with 5 test functions covering "
                        "boundaries; gold_files holds the corrected stub. No BUG/TODO comments; "
                        "the prompt states only the symptom.\n"
                        "Command: python -m pytest -q\n"
                        f"Inspired by: {prompt[:400]}"
                    ),
                }
            ],
            max_tokens=4096,
        )
        data = _extract_json_object(raw_text)
    _coerce_scalar_fields(data)
    data.setdefault("metadata", {})
    data["metadata"]["generation"] = "llm"
    data["metadata"]["review_status"] = "needs_review"
    data["metadata"]["split"] = "auto"
    data["metadata"]["weak_grader"] = False
    if draft.get("metadata", {}).get("source_session_id"):
        data["metadata"]["source_session_id"] = draft["metadata"]["source_session_id"]
        data["metadata"]["source"] = "llm_chat_records"

    grader = data.get("grader") or {}
    if grader.get("mode") != "script":
        # force script if tests exist
        test_files = [
            f["path"]
            for f in ((data.get("context") or {}).get("files") or [])
            if str(f.get("path", "")).startswith("test_") or "/test_" in str(f.get("path", ""))
        ]
        if test_files:
            grader = {
                "mode": "script",
                "command": f"python -m pytest -q {test_files[0]}",
            }
    if grader.get("mode") == "script" and not is_safe_grader_command(grader.get("command")):
        raise ValueError(f"unsafe grader command: {grader.get('command')}")
    data["grader"] = grader

    # Light normalize without destroying LLM script grader
    data["prompt"] = redact_secrets(str(data.get("prompt") or ""))
    ctx = data.setdefault("context", {})
    cleaned = []
    for f in ctx.get("files") or []:
        entry = {
            "path": f["path"],
            "content": redact_secrets(str(f.get("content") or "")),
        }
        if f.get("role"):
            entry["role"] = str(f["role"])
        cleaned.append(entry)
    if not cleaned:
        raise ValueError("LLM case has no context.files")
    ctx["files"] = cleaned
    grader["gold_files"] = [
        {"path": g["path"], "content": redact_secrets(str(g.get("content") or ""))}
        for g in grader.get("gold_files") or []
        if g.get("path")
    ]

    # A prompt that names the mechanism turns any tier above T1 into a giveaway. One rewrite
    # attempt, then settle_tier decides what the case actually qualifies as.
    if target_tier != "T1" and find_disclosures(data["prompt"]):
        try:
            data["prompt"] = redact_secrets(_delocalize_prompt(data["prompt"], chat=_chat))
        except Exception as e:
            print(f"delocalize failed for {data.get('case_id')}: {e}")

    if not data.get("case_id"):
        data["case_id"] = f"auto-{task_fingerprint(data['prompt'], [f['path'] for f in cleaned])}"

    settled, notes = settle_tier(data, target_tier)
    data["metadata"]["generation"] = "llm"
    data["metadata"]["tier_requested"] = target_tier
    data["metadata"]["tier_notes"] = notes
    data["metadata"]["weak_grader"] = data.get("grader", {}).get("mode") != "script"
    if not settled:
        raise ValueError(f"case satisfies no tier: {notes}")
    return data
