from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any

import httpx

from aibench.agents.base import AgentAdapter, request_timeout_s
from aibench.models import AgentRunResult, Case, StepRecord, UsageRecord


def _strip_fences(text: str) -> str:
    text = text.strip()
    m = re.match(r"^```(?:json)?\s*([\s\S]*?)\s*```$", text)
    if m:
        return m.group(1).strip()
    return text


def _parse_files_payload(text: str) -> tuple[list[dict[str, str]], str]:
    """Read the model's answer, tolerating the ways a real model wraps it.

    ``json.loads`` on the whole reply is too brittle to be measuring anything but itself. In a
    five-model ablation over 31 cases, 22 of GLM-5.2's 23 failures were this parse raising
    "Expecting value: line 1 column 1" — the model never got credit for an answer it may well
    have produced, while only 1 failure was a real attempt that did not pass. Models whose
    replies carry more prose around the object failed more, which reads as a capability
    difference and is not one.

    So: strip fences, fall back to the outermost {...} in the text, and parse non-strictly so a
    literal newline inside a string value — the ordinary way to write a source file into JSON —
    is not fatal.
    """
    cleaned = _strip_fences(text)
    try:
        data = json.loads(cleaned, strict=False)
    except json.JSONDecodeError:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start < 0 or end <= start:
            raise
        data = json.loads(cleaned[start : end + 1], strict=False)
    if not isinstance(data, dict) or "files" not in data:
        raise ValueError("model JSON must be an object with key 'files'")
    files = data["files"]
    if not isinstance(files, list):
        raise ValueError("'files' must be a list")
    out: list[dict[str, str]] = []
    for item in files:
        if not isinstance(item, dict) or "path" not in item or "content" not in item:
            raise ValueError("each file needs path and content")
        out.append({"path": str(item["path"]), "content": str(item["content"])})
    message = str(data.get("message") or "")
    return out, message


class OpenAICompatAgent(AgentAdapter):
    """Single-turn OpenAI-compatible agent: model rewrites files as JSON."""

    def run(
        self,
        case: Case,
        workspace: Path,
        *,
        max_steps: int,
        max_wall_time_s: float,
    ) -> AgentRunResult:
        t0 = time.perf_counter()
        steps: list[StepRecord] = []
        model = self.model_config
        api_key_env = model.api_key_env or "AIBENCH_API_KEY"
        api_key = os.environ.get(api_key_env) or os.environ.get("OPENAI_API_KEY")
        if not api_key:
            return AgentRunResult(
                status="infra_error",
                error_message=f"missing API key env: {api_key_env} or OPENAI_API_KEY",
                wall_time_s=time.perf_counter() - t0,
            )
        base_url = (
            model.base_url
            or os.environ.get("OPENAI_BASE_URL")
            or os.environ.get("AIBENCH_BASE_URL")
            or "https://api.openai.com/v1"
        ).rstrip("/")
        # Config wins over env, same precedence as base_url above. The other way round, every
        # row of a multi-model ablation would silently run whatever OPENAI_MODEL happens to be
        # while the report still labels them apart.
        model_name = model.model or os.environ.get("OPENAI_MODEL")
        system = self.agent_config.options.get("system_prompt") or (
            'Return JSON {"files":[...],"message":"..."} only.'
        )
        max_tokens = int(self.agent_config.options.get("max_tokens", model.max_tokens))

        file_blob = "\n\n".join(f"### {fb.path}\n```\n{fb.content}\n```" for fb in case.files)
        user = (
            f"Task:\n{case.prompt}\n\nCurrent files:\n{file_blob}\n\n"
            "Output JSON with updated files only (include full file content)."
        )
        steps.append(StepRecord(step_index=0, action="llm_call", tool="chat.completions"))

        payload: dict[str, Any] = {
            "model": model_name,
            "temperature": model.temperature,
            "max_tokens": max_tokens,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        from aibench.retry import is_retryable_error, retry_call

        def _request_and_parse() -> tuple[dict[str, Any], list[dict[str, str]], str]:
            with httpx.Client(timeout=request_timeout_s(max_wall_time_s)) as client:
                resp = client.post(
                    f"{base_url}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                )
                resp.raise_for_status()
                body_local = resp.json()
            choice = body_local["choices"][0]
            msg = choice["message"]
            content = str(msg.get("content") or "").strip()
            if not content:
                # Only now consider the reasoning stream, and only if the model actually
                # finished. A truncated reply puts *thinking* there; handing that to the JSON
                # parser blames the model for an answer it never got to give.
                if str(choice.get("finish_reason") or "") == "length":
                    raise ValueError(
                        "model exhausted its output budget before answering (finish_reason=length)"
                    )
                content = str(msg.get("reasoning_content") or "").strip()
            if not content:
                raise ValueError("empty content in response")
            files_local, message_local = _parse_files_payload(str(content))
            return body_local, files_local, message_local

        try:
            body, files, message = retry_call(
                _request_and_parse,
                label=f"openai_compat:{case.case_id}",
                retry_if=lambda e: (
                    is_retryable_error(e)
                    or "parse" in str(e).lower()
                    or "json" in str(e).lower()
                    or "empty content" in str(e).lower()
                ),
            )
        except Exception as e:
            err = str(e)
            status = (
                "infra_error" if is_retryable_error(e) or "request" in err.lower() else "failed"
            )
            return AgentRunResult(
                status=status,
                error_message=f"LLM request/parse failed after retries: {e}",
                steps=steps,
                wall_time_s=time.perf_counter() - t0,
                empty_patch=True,
            )

        usage_raw = body.get("usage") or {}
        usage = UsageRecord(
            prompt_tokens=int(usage_raw.get("prompt_tokens") or 0),
            completion_tokens=int(usage_raw.get("completion_tokens") or 0),
            total_tokens=int(
                usage_raw.get("total_tokens")
                or (
                    int(usage_raw.get("prompt_tokens") or 0)
                    + int(usage_raw.get("completion_tokens") or 0)
                )
            ),
            model_calls=1,
        )

        written: list[str] = []
        for item in files:
            rel = item["path"].lstrip("/")
            if ".." in Path(rel).parts:
                continue
            path = workspace / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(item["content"], encoding="utf-8")
            written.append(rel)

        steps.append(
            StepRecord(
                step_index=1,
                action="write_files",
                tool="fs",
                detail=f"wrote {len(written)} files",
            )
        )
        return AgentRunResult(
            status="completed",
            artifacts={
                "files_written": written,
                "final_message": message,
                "patch": None,
            },
            usage=usage,
            steps=steps[:max_steps],
            wall_time_s=time.perf_counter() - t0,
            empty_patch=len(written) == 0,
        )
