"""Measure the model with nothing between it and the grader.

Every other adapter here puts a protocol in the way, and measured against this one the
protocol dominated the result:

* ``openai_compat`` requires the whole file back inside a JSON envelope. Seven of GLM-5.2's
  ten failures in a five-model ablation were that envelope failing to parse, against one that
  was a real attempt failing its tests.
* ``tool_loop`` adds a step budget and takes a different path each run. Across three identical
  repeats it flipped 32.3% of GLM-5.1's cases and 25.8% of Deepseek-V4-Flash's.

Removing both: GLM-5.1's flip rate goes to **0.0%** — three repeats over 31 cases, identical
case by case — and Deepseek's to 22.6%, which is the model's own nondeterminism at
temperature 0. The run-to-run spread falls from 12.9 and 16.1 points to 0.0 and 3.2.

The scaffold also *inverted* the ranking. Bare, GLM-5.1 leads 90.3% to 83.9%; under the tool
loop Deepseek led 90.3% to 71.0%. Same cases, same models, opposite direction.

So this adapter exists to answer "how good is the model", and the scaffolded adapters answer
"how good is this model in this harness". Both are worth measuring; conflating them is what
produced five rounds of contradictory rankings.

The protocol is deliberately the smallest thing that can be graded: one call, the corrected
file in a fenced block, written to the workspace the grader already builds. No JSON, no tools,
no second turn, no step budget.
"""

from __future__ import annotations

import os
import re
import time
from pathlib import Path
from typing import Any

import httpx

from aibench.agents.base import AgentAdapter, request_timeout_s
from aibench.languages import spec_for_path
from aibench.models import AgentConfig, AgentRunResult, Case, ModelConfig, StepRecord, UsageRecord

#: Any fenced block. The largest one wins: models often precede the file with a short fenced
#: snippet of the offending lines, and the answer is the long one.
_FENCE = re.compile(r"```[A-Za-z0-9_+.-]*[ \t]*\r?\n([\s\S]*?)```")

DEFAULT_SYSTEM = (
    "You fix a defect in one source file. Reply with the complete corrected file inside a "
    "single fenced code block and nothing else. No explanation, no JSON, no diff."
)
#: Matches the reverse-construction client. A reasoning model needs room to think first.
DEFAULT_MAX_TOKENS = 16384
DEFAULT_MAX_TOKEN_CEILING = 49152


class TruncatedAnswer(RuntimeError):
    """The model ran out of output budget before emitting an answer."""


def extract_code(text: str) -> str:
    """The fenced block a model replied with, or the whole reply if it skipped the fence."""
    blocks = _FENCE.findall(text or "")
    if blocks:
        return max(blocks, key=len).strip("\n")
    return (text or "").strip()


def _is_test_file(path: str) -> bool:
    """Whether the runner that grades this case would discover ``path`` as a test.

    Consulted alongside `role`, never instead of it: `role` is optional and defaults to `impl`,
    so a case that omits it — every committed `seed-v0` case does — labels its test files as
    implementations.
    """
    spec = spec_for_path(path)
    return bool(spec and spec.is_test_path(path))


class BareModelAgent(AgentAdapter):
    """One call, one file back, no scaffold."""

    def __init__(self, agent_config: AgentConfig, model_config: ModelConfig) -> None:
        super().__init__(agent_config, model_config)
        opts = self.agent_config.options
        self.system = str(opts.get("system_prompt") or DEFAULT_SYSTEM)
        self.max_tokens = int(opts.get("max_tokens", DEFAULT_MAX_TOKENS))
        self.max_token_ceiling = int(
            opts.get("max_tokens_ceiling", max(self.max_tokens * 4, DEFAULT_MAX_TOKEN_CEILING))
        )
        #: Showing the tests is what makes the task well posed rather than a guessing game; the
        #: hidden half of a split suite is still withheld, so this is not the answer key.
        self.show_tests = bool(opts.get("show_tests", True))

    def _prompt(self, case: Case) -> tuple[str, str] | None:
        """The single file to fix, or ``None`` when this adapter cannot pose the case.

        A multi-file case used to be silently reduced to its first implementation file. The
        adapter's own config claims axis **A4 跨文件一致性**, which is precisely the axis a
        one-file prompt cannot exercise — so the run reported a number against an axis the
        submission never saw, and the case's other files were simply absent from the task. It
        refuses now: an unposable case is a harness failure, not a hard one.

        "Implementation file" is decided by filename as well as by label. ``role`` is optional
        and :meth:`FileBlob.from_dict` defaults it to ``impl``, so a case that omits it — which
        all four committed `seed-v0` cases do — has every file counted as an implementation,
        `test_fizzbuzz.py` included. Counting labels alone made two of those four unposable.
        """
        impls = [f for f in case.files if f.role == "impl" and not _is_test_file(f.path)]
        if len(impls) != 1:
            return None
        impl = impls[0]
        test = next(
            (f for f in case.files if f.role == "test" or _is_test_file(f.path)),
            None,
        )
        parts = [
            f"Task: {case.prompt}",
            f"File to fix: {impl.path}\n```\n{impl.content}\n```",
        ]
        if test is not None and self.show_tests:
            parts.append(f"It must satisfy these tests:\n```\n{test.content}\n```")
        parts.append(f"Reply with the complete corrected {impl.path} in one fenced code block.")
        return impl.path, "\n\n".join(parts)

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
        # Config wins over env, so a multi-model matrix cannot silently run one model under
        # five different labels.
        model_name = model.model or os.environ.get("OPENAI_MODEL")

        built = self._prompt(case)
        if built is None:
            n_impl = sum(1 for f in case.files if f.role == "impl")
            return AgentRunResult(
                # `infra_error`, not `failed`: the adapter could not pose the task, which is a
                # statement about this adapter and not about the model. Scoring it as a failure
                # charged the model for a case it was never shown.
                status="infra_error",
                error_message=(
                    f"bare_model needs exactly one impl file; this case has {n_impl}. "
                    "It pastes the whole file into one prompt, so a multi-file case cannot be "
                    "posed — use openai_compat or tool_loop for those."
                ),
                wall_time_s=time.perf_counter() - t0,
                empty_patch=True,
            )
        impl_path, user = built
        steps.append(StepRecord(step_index=0, action="llm_call", tool="chat.completions"))

        from aibench.retry import is_retryable_error, retry_call

        budget = {"value": self.max_tokens}

        def _once() -> tuple[dict[str, Any], str]:
            with httpx.Client(timeout=request_timeout_s(max_wall_time_s)) as client:
                resp = client.post(
                    f"{base_url}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": model_name,
                        "temperature": model.temperature,
                        "max_tokens": budget["value"],
                        "messages": [
                            {"role": "system", "content": self.system},
                            {"role": "user", "content": user},
                        ],
                    },
                )
                resp.raise_for_status()
                body = resp.json()
            choice = body["choices"][0]
            content = str((choice.get("message") or {}).get("content") or "").strip()
            if not content:
                # A truncated reply puts thinking in the reasoning stream, not an answer.
                if str(choice.get("finish_reason") or "") == "length":
                    raise TruncatedAnswer(
                        f"model hit the {budget['value']}-token output cap before answering"
                    )
                content = str((choice.get("message") or {}).get("reasoning_content") or "").strip()
            if not content:
                raise ValueError("empty content in response")
            code = extract_code(content)
            if not code.strip():
                raise ValueError("no code in response")
            return body, code

        def _with_escalation() -> tuple[dict[str, Any], str]:
            while True:
                try:
                    return _once()
                except TruncatedAnswer:
                    if budget["value"] >= self.max_token_ceiling:
                        raise
                    budget["value"] = min(budget["value"] * 2, self.max_token_ceiling)

        try:
            body, code = retry_call(
                _with_escalation,
                label=f"bare_model:{case.case_id}",
                retry_if=lambda e: (
                    is_retryable_error(e)
                    or "empty content" in str(e).lower()
                    or "no code" in str(e).lower()
                ),
            )
        except Exception as e:
            err = str(e)
            status = (
                "infra_error" if is_retryable_error(e) or "request" in err.lower() else "failed"
            )
            return AgentRunResult(
                status=status,
                error_message=f"LLM request failed after retries: {e}",
                steps=steps,
                wall_time_s=time.perf_counter() - t0,
                empty_patch=True,
            )

        raw = body.get("usage") or {}
        usage = UsageRecord(
            prompt_tokens=int(raw.get("prompt_tokens") or 0),
            completion_tokens=int(raw.get("completion_tokens") or 0),
            total_tokens=int(
                raw.get("total_tokens")
                or (int(raw.get("prompt_tokens") or 0) + int(raw.get("completion_tokens") or 0))
            ),
            model_calls=1,
        )

        rel = impl_path.lstrip("/")
        if ".." in Path(rel).parts:
            return AgentRunResult(
                status="failed",
                error_message=f"refusing to write outside the workspace: {impl_path}",
                steps=steps,
                usage=usage,
                wall_time_s=time.perf_counter() - t0,
                empty_patch=True,
            )
        target = workspace / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(code, encoding="utf-8")
        steps.append(
            StepRecord(step_index=1, action="write_files", tool="fs", detail=f"wrote {rel}")
        )
        return AgentRunResult(
            status="completed",
            artifacts={"files_written": [rel], "final_message": "", "patch": None},
            usage=usage,
            steps=steps,
            wall_time_s=time.perf_counter() - t0,
            empty_patch=False,
        )
