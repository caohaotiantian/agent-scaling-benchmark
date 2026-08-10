"""Optional LLM soft filter for coding-benchmark suitability."""

from __future__ import annotations

import json
import re
from typing import Any

import httpx

from aibench.env_config import openai_settings
from aibench.extract.filter_rules import FilterDecision

_DISCLOSURE_SYSTEM = (
    "You review coding-task descriptions for a benchmark. Decide whether the text reveals the "
    "CAUSE of the defect rather than only its observable SYMPTOM.\n"
    "Disclosed: names the faulty mechanism, the line, the variable, or the fix "
    "('the comparison is inverted', 'it uses == instead of is', 'change v4 to v5').\n"
    "Not disclosed: describes what was expected and what happened "
    "('once one IP is limited, all IPs are blocked'), or states the required behaviour.\n"
    'Reply ONLY JSON: {"disclosed": true|false, "reason": "..."}'
)


def llm_disclosure_verdict(prompt: str, *, timeout_s: float = 60.0) -> tuple[bool | None, str]:
    """Second opinion on whether a prompt gives the defect away.

    The regex detector in :mod:`aibench.tiers` stays the primary judge — it is deterministic
    and free. This catches paraphrased disclosure the patterns miss. Returns ``(None, reason)``
    when unavailable, so a missing key or a bad response never turns into a verdict.
    """
    settings = openai_settings()
    if not settings["api_key"] or not settings["base_url"] or not settings["model"]:
        return None, "llm_disclosure_skipped_no_api"

    base = settings["base_url"].rstrip("/")
    try:
        with httpx.Client(timeout=timeout_s) as client:
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
                        {"role": "system", "content": _DISCLOSURE_SYSTEM},
                        {"role": "user", "content": (prompt or "")[:2000]},
                    ],
                },
            )
            resp.raise_for_status()
            msg = resp.json()["choices"][0]["message"]
            content = (msg.get("content") or msg.get("reasoning_content") or "").strip()
    except Exception as e:
        return None, f"llm_disclosure_unavailable:{e}"

    data = _parse_json_object(content)
    if data is None or "disclosed" not in data:
        return None, f"llm_disclosure_parse_fail:{content[:120]}"
    return bool(data["disclosed"]), str(data.get("reason") or "")


def _parse_json_object(text: str) -> dict[str, Any] | None:
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", (text or "").strip())
    start, end = raw.find("{"), raw.rfind("}")
    try:
        return json.loads(raw[start : end + 1] if start >= 0 else raw)
    except Exception:
        return None


def llm_soft_filter_draft(draft: dict[str, Any], *, timeout_s: float = 60.0) -> FilterDecision:
    settings = openai_settings()
    if not settings["api_key"] or not settings["base_url"] or not settings["model"]:
        return FilterDecision(True, "llm_filter_skipped_no_api", 0.0, ["no_api"])

    prompt = (draft.get("prompt") or "")[:1200]
    system = (
        "You classify whether a task is suitable for an automated coding benchmark "
        "with unit tests. Reply ONLY JSON: "
        '{"keep": true|false, "reason": "...", "task_type": "feature|bugfix|other"}'
    )
    user = (
        f"Task:\n{prompt}\n\n"
        "Keep only if it can be reduced to a small coding fix/implement with tests. "
        "Drop ops/explain/chat/judge-meta tasks."
    )
    base = settings["base_url"].rstrip("/")
    with httpx.Client(timeout=timeout_s) as client:
        resp = client.post(
            f"{base}/chat/completions",
            headers={
                "Authorization": f"Bearer {settings['api_key']}",
                "Content-Type": "application/json",
            },
            json={
                "model": settings["model"],
                "temperature": 0,
                "max_tokens": 256,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            },
        )
        resp.raise_for_status()
        msg = resp.json()["choices"][0]["message"]
        content = (msg.get("content") or msg.get("reasoning_content") or "").strip()

    text = content
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        start, end = text.find("{"), text.rfind("}")
        data = json.loads(text[start : end + 1] if start >= 0 else text)
    except Exception as e:
        return FilterDecision(True, f"llm_filter_parse_fail:{e}", 0.0, ["parse_fail"])

    keep = bool(data.get("keep"))
    reason = str(data.get("reason") or ("llm_keep" if keep else "llm_drop"))
    labels = ["llm_soft_filter", str(data.get("task_type") or "")]
    return FilterDecision(keep, reason, 1.0 if keep else 0.0, labels)
