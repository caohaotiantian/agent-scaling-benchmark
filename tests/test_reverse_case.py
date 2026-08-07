"""Building a case around a defect the model is not allowed to invent.

The gateway client gets most of the attention here. The first reverse run over real traces
built 2 cases from 22 usable drafts, and every one of the 20 losses was the model spending its
whole output budget on reasoning and never answering. The client then returned the reasoning
transcript as if it were the answer, so the failures surfaced as ten "Expecting property name"
and eight "no JSON object" — the JSON parser's opinion of English prose, naming neither the
cause nor the fix.
"""

import json

import pytest

from aibench.extract.reverse_case import (
    DEFAULT_MAX_TOKENS,
    MAX_MAX_TOKENS,
    TruncatedAnswer,
    build_prompt,
    chat_json,
    iter_file_versions,
    reverse_case_from_versions,
)

SETTINGS = {"base_url": "https://gw.invalid/v1", "api_key": "k", "model": "m"}


class _Resp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def _reply(*, content="", reasoning="", finish_reason="stop"):
    return {
        "choices": [
            {
                "finish_reason": finish_reason,
                "message": {"content": content, "reasoning_content": reasoning},
            }
        ]
    }


def _install(monkeypatch, replies):
    """Serve `replies` in order, recording the max_tokens each request asked for."""
    seen: list[int] = []
    import httpx

    class _Client:
        def __init__(self, *a, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, _url, *, headers=None, json=None):
            seen.append(json["max_tokens"])
            return _Resp(replies[min(len(seen) - 1, len(replies) - 1)])

    monkeypatch.setattr(httpx, "Client", _Client)
    return seen


def test_truncated_answer_escalates_the_budget_and_succeeds(monkeypatch):
    seen = _install(
        monkeypatch,
        [_reply(reasoning="Let me think...", finish_reason="length"), _reply(content='{"ok": 1}')],
    )
    assert chat_json(SETTINGS)([{"role": "user", "content": "hi"}]) == '{"ok": 1}'
    assert seen == [DEFAULT_MAX_TOKENS, DEFAULT_MAX_TOKENS * 2]


def test_truncation_never_passes_reasoning_off_as_the_answer(monkeypatch):
    """The exact bug: prose reaching the JSON parser, which then blames the model."""
    _install(monkeypatch, [_reply(reasoning="The user wants me to...", finish_reason="length")])
    with pytest.raises(TruncatedAnswer):
        chat_json(SETTINGS)([{"role": "user", "content": "hi"}])


def test_escalation_stops_rather_than_spending_without_bound(monkeypatch):
    seen = _install(monkeypatch, [_reply(reasoning="thinking", finish_reason="length")])
    with pytest.raises(TruncatedAnswer):
        chat_json(SETTINGS)([{"role": "user", "content": "hi"}])
    assert seen[-1] == MAX_MAX_TOKENS
    assert all(t <= MAX_MAX_TOKENS for t in seen)


def test_reasoning_is_still_accepted_when_the_model_actually_finished(monkeypatch):
    """Some gateways put the whole answer in reasoning_content; only truncation is the tell."""
    _install(monkeypatch, [_reply(reasoning='{"ok": 1}', finish_reason="stop")])
    assert chat_json(SETTINGS)([{"role": "user", "content": "hi"}]) == '{"ok": 1}'


PRE = "def clamp(v, lo, hi):\n    return min(v, hi)\n"
POST = "def clamp(v, lo, hi):\n    return max(lo, min(v, hi))\n"
FV = {"path": "pkg/clamp.py", "pre": PRE, "post": POST, "edits": 1}


def _draft(**over):
    d = {"case_id": "db-1", "prompt": "clamp ignores the lower bound", "metadata": {}}
    d.update(over)
    return d


def _chat_returning(obj):
    return lambda _messages: json.dumps(obj)


def test_stub_is_the_pre_edit_file_and_gold_is_the_post_edit_file():
    case = reverse_case_from_versions(
        FV,
        draft=_draft(),
        chat=_chat_returning(
            {
                "prompt": "Values below the lower bound are returned unchanged.",
                "test_path": "test_clamp.py",
                "test_content": "def test_low():\n    assert True\n",
            }
        ),
    )
    impl = next(f for f in case["context"]["files"] if f["role"] == "impl")
    assert impl["content"] == PRE
    assert case["grader"]["gold_files"][0]["content"] == POST
    assert case["metadata"]["generation"] == "reverse"


def test_unparseable_test_file_is_refused_rather_than_shipped():
    with pytest.raises(ValueError, match="does not parse"):
        reverse_case_from_versions(
            FV,
            draft=_draft(),
            chat=_chat_returning(
                {
                    "prompt": "Values below the lower bound are returned unchanged.",
                    "test_path": "test_clamp.py",
                    "test_content": "def test_low(:\n",
                }
            ),
        )


def test_unchanged_pair_is_not_a_defect():
    with pytest.raises(ValueError, match="empty or unchanged"):
        reverse_case_from_versions(
            {"path": "pkg/clamp.py", "pre": PRE, "post": PRE},
            draft=_draft(),
            chat=_chat_returning({}),
        )


def test_prompt_carries_both_versions_and_the_diff():
    _system, user = build_prompt(FV, "clamp ignores the lower bound")
    assert PRE in user and POST in user
    assert "WHAT CHANGED" in user and "max(lo, min(v, hi))" in user


def test_versions_are_offered_largest_change_first():
    draft = _draft(
        metadata={
            "file_versions": [
                {"path": "small.py", "pre": "a = 1\n", "post": "a = 2\n"},
                {"path": "big.py", "pre": "a = 1\n", "post": "a = 1\n" + "b = 2\n" * 40},
            ]
        }
    )
    assert [fv["path"] for fv in iter_file_versions(draft)] == ["big.py", "small.py"]


def test_a_pair_needing_absent_packages_is_dropped_before_generation():
    """The 41% loss, and worse: it also let cases pass the stub gate for the wrong reason."""
    draft = _draft(
        metadata={
            "file_versions": [
                {"path": "a.py", "pre": "import numpy\nx=1\n", "post": "import numpy\nx=2\n"},
                {"path": "b.py", "pre": "import json\nx=1\n", "post": "import json\nx=2\n"},
            ]
        }
    )
    assert [fv["path"] for fv in iter_file_versions(draft)] == ["b.py"]
    assert len(iter_file_versions(draft, require_imports_satisfiable=False)) == 2


def test_a_sibling_import_counts_as_unsatisfiable_for_js():
    """A one-file workspace has no siblings, so `./util` is as absent as a bare package."""
    from aibench.extract.file_versions import unsatisfiable_imports

    assert unsatisfiable_imports("m.ts", "import {a} from './util';") == {"./util"}
    assert unsatisfiable_imports("m.ts", "import {a} from 'lodash';") == {"lodash"}
    assert unsatisfiable_imports("m.ts", "import fs from 'node:fs';") == set()


def test_python_stdlib_and_installed_packages_are_satisfiable():
    from aibench.extract.file_versions import unsatisfiable_imports

    assert unsatisfiable_imports("m.py", "import json\nimport pytest\n") == set()
    assert unsatisfiable_imports("m.py", "import definitely_not_installed_xyz\n") == {
        "definitely_not_installed_xyz"
    }
