"""Generate schema-shaped benchmark cases from drafts/sessions (heuristic + LLM)."""

from __future__ import annotations

import json
import re
from typing import Any

import httpx

from aibench.env_config import openai_settings
from aibench.extract.history_parse import guess_language, guess_task_type
from aibench.extract.sessions import redact_secrets, task_fingerprint

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


def heuristic_case_from_draft(draft: dict[str, Any]) -> dict[str, Any]:
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
    meta["weak_grader"] = bool(meta_weak) if grader.get("mode") == "gold" else grader.get("mode") != "script"
    case["schema_version"] = case.get("schema_version") or "0.1"
    if not case.get("task_type"):
        case["task_type"] = guess_task_type(case["prompt"])
    if not case.get("language"):
        case["language"] = guess_language(case["prompt"], cleaned, [])
    if not case.get("case_id"):
        fp = task_fingerprint(case["prompt"], [f["path"] for f in cleaned])
        case["case_id"] = f"auto-{fp}"
    return case


def _is_useful_key_line(s: str) -> bool:
    if not s or len(s) < 4:
        return False
    # drop markdown trees, absolute paths, ascii art
    if s.startswith(("├", "│", "└", "┌", "─", "/Users/", "C:\\", "open ")):
        return False
    if "python-algorithms/" in s or s in {"↓", "{", "}"}:
        return False
    return True


def _default_key_lines(content: str) -> list[str]:
    lines = []
    for ln in content.splitlines():
        s = ln.strip()
        if not _is_useful_key_line(s):
            continue
        if any(k in s for k in ("def ", "class ", "function ", "return ", "import ", "public ", "fn ")):
            lines.append(s[:100])
        if len(lines) >= 3:
            break
    return lines


def generate_case_with_llm(
    draft: dict[str, Any],
    *,
    timeout_s: float = 90.0,
) -> dict[str, Any]:
    """Ask LLM to produce a minimal self-contained coding case JSON."""
    settings = openai_settings()
    if not settings["api_key"] or not settings["base_url"] or not settings["model"]:
        raise RuntimeError("OPENAI_API_KEY / OPENAI_BASE_URL / OPENAI_MODEL required for LLM generate")

    prompt = draft.get("prompt") or ""
    files = ((draft.get("context") or {}).get("files")) or []
    file_summaries = []
    for f in files[:6]:
        body = (f.get("content") or "")[:1500]
        file_summaries.append(f"### {f.get('path')}\n{body}")

    system = (
        "You generate AI-coding-assist benchmark cases. "
        "Return ONLY a JSON object with keys: case_id, schema_version, task_type, language, "
        "prompt, context, grader, metadata. "
        "context.files is an array of {path, content}. "
        "Prefer grader.mode=script with command like 'python -m pytest -q test_xxx.py' "
        "and include the test file in context.files. "
        "Keep the task small and self-contained. No markdown fences."
    )
    user = (
        f"Source user task:\n{prompt[:3000]}\n\n"
        f"Known files (may be incomplete):\n{chr(10).join(file_summaries) or '(none)'}\n\n"
        "Produce a minimal reproducible case. Desensitize secrets."
    )
    base = settings["base_url"].rstrip("/")
    payload = {
        "model": settings["model"],
        "temperature": 0,
        "max_tokens": 4096,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
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
        content = resp.json()["choices"][0]["message"]["content"]

    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("LLM did not return a JSON object")

    # safety + defaults
    data.setdefault("schema_version", "0.1")
    data.setdefault("metadata", {})
    data["metadata"]["generation"] = "llm"
    data["metadata"]["review_status"] = "needs_review"
    data["metadata"]["split"] = "auto"
    if draft.get("metadata", {}).get("source_session_id"):
        data["metadata"]["source_session_id"] = draft["metadata"]["source_session_id"]
        data["metadata"]["source"] = "llm_chat_records"

    grader = data.get("grader") or {}
    if grader.get("mode") == "script" and not is_safe_grader_command(grader.get("command")):
        raise ValueError(f"unsafe grader command: {grader.get('command')}")

    # ensure required shapes
    data = heuristic_case_from_draft(data)
    data["metadata"]["generation"] = "llm"
    return data
