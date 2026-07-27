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
            raise ValueError(f"no JSON object in LLM content: {raw[:200]!r}")
        data = json.loads(raw[start : end + 1])
    if not isinstance(data, dict):
        raise ValueError("LLM JSON root must be object")
    return data


def _normalize_task_type(value: Any) -> str:
    s = str(value or "feature").strip().lower().replace("-", "_").replace(" ", "_")
    if s in {"bugfix", "feature", "refactor", "explain_to_edit", "test_gen", "pairwise"}:
        return s
    return _TASK_TYPE_MAP.get(s, "feature")


def generate_case_with_llm(
    draft: dict[str, Any],
    *,
    timeout_s: float = 120.0,
) -> dict[str, Any]:
    """Ask LLM to produce a minimal self-contained coding case JSON with script grader."""
    settings = openai_settings()
    if not settings["api_key"] or not settings["base_url"] or not settings["model"]:
        raise RuntimeError("OPENAI_API_KEY / OPENAI_BASE_URL / OPENAI_MODEL required for LLM generate")

    prompt = draft.get("prompt") or ""
    files = ((draft.get("context") or {}).get("files")) or []
    file_summaries = []
    for f in files[:3]:
        body = (f.get("content") or "")[:600]
        file_summaries.append(f"### {f.get('path')}\n{body}")

    system = (
        "You create tiny self-contained Python coding benchmark cases.\n"
        "Return ONLY one JSON object (markdown fences allowed).\n"
        "Required keys: case_id, schema_version, task_type, language, prompt, context, grader, metadata.\n"
        "task_type MUST be one of: bugfix, feature, refactor, explain_to_edit, test_gen, pairwise.\n"
        "language should be python.\n"
        "context.files: array of {path, content}. Include:\n"
        "  1) a stub implementation that is incomplete/wrong\n"
        "  2) a pytest file that fails on the stub and passes on a correct fix\n"
        "grader: {\"mode\":\"script\",\"command\":\"python -m pytest -q <test_file>.py\"}\n"
        "Keep files short (<80 lines each). No secrets. Abstract away private paths."
    )
    user = (
        f"Inspire a MINIMAL coding task from this real user request "
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
                        "Schema: case_id, schema_version=0.1, task_type=feature, language=python,\n"
                        "prompt, context.files=[{path,content}], grader={mode:script,command},\n"
                        "metadata={}.\n"
                        "Include stub .py + test_*.py. Command: python -m pytest -q test_xxx.py\n"
                        f"Inspired by: {prompt[:400]}"
                    ),
                }
            ],
            max_tokens=4096,
        )
        data = _extract_json_object(raw_text)
    data["task_type"] = _normalize_task_type(data.get("task_type"))
    data["language"] = data.get("language") or "python"
    data.setdefault("schema_version", "0.1")
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
        cleaned.append(
            {
                "path": f["path"],
                "content": redact_secrets(str(f.get("content") or "")),
            }
        )
    if not cleaned:
        raise ValueError("LLM case has no context.files")
    ctx["files"] = cleaned
    if not data.get("case_id"):
        data["case_id"] = f"auto-{task_fingerprint(data['prompt'], [f['path'] for f in cleaned])}"
    data["metadata"]["generation"] = "llm"
    data["metadata"]["weak_grader"] = data.get("grader", {}).get("mode") != "script"
    return data
