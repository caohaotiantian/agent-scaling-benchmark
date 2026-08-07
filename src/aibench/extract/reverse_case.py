"""Build a case whose defect is the one an engineer actually made.

Ordinary generation asks a model to invent a benchmark case, and it answers with a two-file,
thirty-line exercise no matter what it was given — three separate interventions failed to move
the difficulty of what comes out. Here the defect is not up for negotiation: the stub is the
file as the trace found it, the reference solution is the file as the trace left it, and the
model's only job is to write tests that tell the two apart.

That job is verifiable without any new machinery. ``check_stub_fails`` already requires the
tests to fail on the stub and ``check_reference_solution`` requires them to pass on the gold,
so a model that writes tests which do not separate the two versions produces a case the
existing gates reject. The model cannot make the task easier, only fail to describe it.
"""

from __future__ import annotations

import re
from typing import Any

from aibench.extract.sessions import redact_source, task_fingerprint
from aibench.languages import spec_for_path

#: Enough of each version for the model to see the change without paying for whole large files.
_MAX_FILE_CHARS = 6000


def _unified_ish_diff(pre: str, post: str, limit: int = 60) -> str:
    """The changed lines, so the model is told what the fix was rather than made to find it."""
    import difflib

    lines = list(
        difflib.unified_diff(
            pre.splitlines(), post.splitlines(), fromfile="before", tofile="after", lineterm="", n=2
        )
    )
    return "\n".join(lines[:limit])


def build_prompt(fv: dict[str, Any], user_request: str) -> tuple[str, str]:
    """The system and user messages that ask for tests and a symptom-only description."""
    system = (
        "You are given a source file BEFORE and AFTER a real bug fix, and the request the "
        "engineer was working on. Write a pytest file that FAILS on the BEFORE version and "
        "PASSES on the AFTER version, plus a short task description.\n"
        "Return ONLY one JSON object with keys: prompt, test_path, test_content.\n"
        "- test_content: a complete pytest file, at least 4 test functions, importing the "
        "module under test by its module name. Cover the boundary the fix changed and at "
        "least one case that already worked, so a solver cannot pass by rewriting blindly.\n"
        "- prompt: describes ONLY the observable symptom — what was expected and what "
        "happened. Never name the cause, the mechanism, the line, or the function that "
        "changes. Do not mention 'before' or 'after'.\n"
        "Do not restate or fix the code. The fix already exists; you are writing the test that "
        "would have caught it."
    )
    path = str(fv.get("path") or "module.py")
    module = re.split(r"[\\/]", path)[-1].rsplit(".", 1)[0]
    user = (
        f"Engineer's request:\n{user_request[:600]}\n\n"
        f"File: {path}  (import it as `{module}`)\n\n"
        f"--- BEFORE ---\n{str(fv.get('pre') or '')[:_MAX_FILE_CHARS]}\n\n"
        f"--- AFTER ---\n{str(fv.get('post') or '')[:_MAX_FILE_CHARS]}\n\n"
        f"--- WHAT CHANGED ---\n{_unified_ish_diff(str(fv.get('pre') or ''), str(fv.get('post') or ''))}\n\n"
        "Output the JSON now."
    )
    return system, user


def reverse_case_from_versions(
    fv: dict[str, Any],
    *,
    draft: dict[str, Any],
    chat: Any,
    tier: str = "T2",
) -> dict[str, Any]:
    """Assemble a case from a real before/after pair plus model-written tests.

    Raises when the model's answer cannot be used. The caller treats that as a failed
    generation rather than falling back to something weaker, because the whole point is that
    this defect came from a trace and not from a model.
    """
    from aibench.extract.generate_case import _extract_json_object

    path = str(fv.get("path") or "")
    spec = spec_for_path(path)
    if spec is None:
        raise ValueError(f"no registered runner for {path!r}")
    pre, post = str(fv.get("pre") or ""), str(fv.get("post") or "")
    if not pre or not post or pre == post:
        raise ValueError("before/after pair is empty or unchanged")

    system, user = build_prompt(fv, str(draft.get("prompt") or ""))
    data = _extract_json_object(
        chat(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ]
        )
    )

    test_content = str(data.get("test_content") or "")
    if not test_content.strip():
        raise ValueError("model returned no test_content")
    if spec.parses(test_content) is False:
        raise ValueError("model's test file does not parse")

    module = re.split(r"[\\/]", path)[-1]
    test_path = str(data.get("test_path") or f"test_{module}")
    if not spec.is_test_path(test_path):
        test_path = f"test_{module}"

    prompt = redact_source(str(data.get("prompt") or "").strip())
    if len(prompt) < 20:
        raise ValueError("model returned no usable prompt")

    impl_name = module
    case_id = f"rev-{task_fingerprint(prompt, [impl_name])}"
    return {
        "case_id": case_id,
        "schema_version": "0.1",
        "task_type": "bugfix",
        "language": spec.name,
        "prompt": prompt,
        "context": {
            "files": [
                {"path": impl_name, "content": redact_source(pre, path=impl_name), "role": "impl"},
                {
                    "path": test_path,
                    "content": redact_source(test_content, path=test_path),
                    "role": "test",
                },
            ],
            "notes": f"reverse-constructed from {draft.get('case_id')}; defect is a real edit",
        },
        "grader": {
            "mode": "script",
            "command": "python -m pytest -q" if spec.name == "python" else "node --test",
            "gold_files": [{"path": impl_name, "content": redact_source(post, path=impl_name)}],
        },
        "metadata": {
            "generation": "reverse",
            "source": "llm_chat_records",
            "source_session_id": (draft.get("metadata") or {}).get("source_session_id"),
            "review_status": "needs_review",
            "split": "auto",
            "weak_grader": False,
            "tier_requested": tier,
            "reverse_source_path": path,
            "reverse_edits": fv.get("edits"),
        },
    }


#: A reasoning model spends its budget thinking before it answers, and the answer here is a
#: whole test file. Measured on a real draft: at 4096 the model used every token on reasoning
#: and returned empty content; the same draft at 16384 finished in 3996 with valid JSON.
DEFAULT_MAX_TOKENS = 16384
#: Escalation ceiling, so a model that reasons without converging fails instead of costing more.
MAX_MAX_TOKENS = 49152


class TruncatedAnswer(RuntimeError):
    """The model ran out of output budget before emitting an answer.

    Distinct from a malformed answer, because it says what to do about it: spend more tokens.
    The first reverse run lost 20 of 22 drafts to this and reported it as ten "Expecting
    property name" plus eight "no JSON object" — the JSON parser's opinion of a reasoning
    transcript, which named neither the cause nor the fix.
    """


def chat_json(settings: dict[str, Any], *, timeout_s: float = 300.0) -> Any:
    """A `chat(messages) -> str` bound to the configured gateway."""
    import httpx

    from aibench.retry import retry_call

    base = str(settings["base_url"]).rstrip("/")

    def _ask(messages: list[dict[str, str]], max_tokens: int) -> str:
        def _once() -> str:
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
                        "max_tokens": max_tokens,
                        "messages": messages,
                    },
                )
                resp.raise_for_status()
                choice = resp.json()["choices"][0]
                msg = choice.get("message") or {}
                content = str(msg.get("content") or "").strip()
                if content:
                    return content
                # Only now consider the reasoning stream. Some gateways put the whole answer
                # there, but a truncated response puts *thinking* there, and returning that
                # hands prose to a JSON parser and blames the model for malformed output.
                if str(choice.get("finish_reason") or "") == "length":
                    raise TruncatedAnswer(
                        f"model hit the {max_tokens}-token output cap before answering"
                    )
                reasoning = str(msg.get("reasoning_content") or "").strip()
                if not reasoning:
                    raise ValueError("empty content from gateway")
                return reasoning

        return retry_call(_once, label="reverse_case_chat")

    def _chat(messages: list[dict[str, str]], max_tokens: int = DEFAULT_MAX_TOKENS) -> str:
        budget = max_tokens
        while True:
            try:
                return _ask(messages, budget)
            except TruncatedAnswer:
                if budget >= MAX_MAX_TOKENS:
                    raise
                budget = min(budget * 2, MAX_MAX_TOKENS)

    return _chat


def iter_file_versions(
    draft: dict[str, Any], *, require_imports_satisfiable: bool = True
) -> list[dict[str, Any]]:
    """The before/after pairs a draft carries, largest change first.

    Pairs whose file needs imports a one-file workspace cannot provide are dropped here rather
    than after generation, because generation is the expensive step and the predicate is exact:
    of 22 cases built without it, all 16 with an unsatisfiable import failed the validity gate,
    and 5 of them had first passed it for the wrong reason.
    """
    from aibench.extract.file_versions import unsatisfiable_imports

    fvs = [
        fv
        for fv in ((draft.get("metadata") or {}).get("file_versions") or [])
        if isinstance(fv, dict)
    ]
    if require_imports_satisfiable:
        fvs = [
            fv
            for fv in fvs
            if not (
                unsatisfiable_imports(str(fv.get("path") or ""), str(fv.get("pre") or ""))
                | unsatisfiable_imports(str(fv.get("path") or ""), str(fv.get("post") or ""))
            )
        ]
    return sorted(
        fvs,
        key=lambda f: -abs(len(str(f.get("post") or "")) - len(str(f.get("pre") or ""))),
    )
