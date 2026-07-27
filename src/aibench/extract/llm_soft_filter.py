"""Optional LLM soft filter for coding-benchmark suitability."""

from __future__ import annotations

import json
import re
from typing import Any

import httpx

from aibench.env_config import openai_settings
from aibench.extract.filter_rules import FilterDecision


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
