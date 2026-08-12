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
#: Material only counts when the trace read the file in full — see `_with_vouched_pre`.
READ = {"pre_origin": "read_complete"}


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
                {"path": "small.py", "pre": "a = 1\n", "post": "a = 2\n", **READ},
                {
                    "path": "big.py",
                    "pre": "a = 1\n",
                    "post": "a = 1\n" + "b = 2\n" * 40,
                    **READ,
                },
            ]
        }
    )
    assert [fv["path"] for fv in iter_file_versions(draft)] == ["big.py", "small.py"]


def test_a_pair_needing_absent_packages_is_dropped_before_generation():
    """The 41% loss, and worse: it also let cases pass the stub gate for the wrong reason.

    `obstacle_avoidance` is a private module out of the corpus, not a package anyone can
    install — the category `configs/grading-env.yaml` cannot rescue.
    """
    draft = _draft(
        metadata={
            "file_versions": [
                {
                    "path": "a.py",
                    "pre": "import obstacle_avoidance\nx=1\n",
                    "post": "import obstacle_avoidance\nx=2\n",
                    **READ,
                },
                {
                    "path": "b.py",
                    "pre": "import json\nx=1\n",
                    "post": "import json\nx=2\n",
                    **READ,
                },
            ]
        }
    )
    assert [fv["path"] for fv in iter_file_versions(draft)] == ["b.py"]
    assert len(iter_file_versions(draft, require_imports_satisfiable=False)) == 2


def test_a_pre_the_tool_wrote_is_not_material_for_a_case():
    """The stub is meant to be the file as the trace found it.

    When no read ever happened, `pre` is what the model's own `write` put there, so the "defect"
    is a flaw in code it authored moments earlier. 41 of the 92 usable pairs in the current pool
    are this shape, and nothing distinguished them.
    """
    draft = {
        "metadata": {
            "file_versions": [
                {"path": "m.py", "pre": "v = 1\n", "post": "v = 2\n", "pre_origin": "tool_write"},
                {
                    "path": "n.py",
                    "pre": "v = 1\n",
                    "post": "v = 2\n",
                    "pre_origin": "read_complete",
                },
            ]
        }
    }
    assert [fv["path"] for fv in iter_file_versions(draft)] == ["n.py"]
    assert len(iter_file_versions(draft, require_read_pre=False)) == 2


def test_a_draft_predating_provenance_is_judged_by_the_footer_it_still_carries():
    """All 3,312 drafts on disk were built before `pre_origin` existed.

    They are not exempt: an unvouched-for `pre` is the thing this predicate refuses. They do not
    need to be, either — the read tool's footer is still sitting in the stored text, which is
    how the pool was classified in the first place (346 complete / 113 window / 278 no footer).
    """
    fvs = [
        {"path": "ok.py", "pre": "v = 1\n\n(End of file - total 1 lines)", "post": "v = 2\n"},
        {
            "path": "frag.py",
            "pre": "v = 1\n\n(Showing lines 1-1 of 90. Use offset=2 to continue.)",
            "post": "v = 2\n",
        },
        {"path": "unknown.py", "pre": "v = 1\n", "post": "v = 2\n"},
    ]
    draft = {"metadata": {"file_versions": fvs}}
    kept = iter_file_versions(draft)
    assert [fv["path"] for fv in kept] == ["ok.py"]
    assert kept[0]["pre"] == "v = 1\n", "the footer is not part of the file"
    assert len(iter_file_versions(draft, require_read_pre=False)) == 3


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


def test_the_prompt_states_the_flat_workspace_the_case_actually_ships():
    """Tests referenced the original repo layout, which the case does not reproduce.

    Observed in one build: `from src.odoo_client.config_loader import ...`, `./lifecycleTypes.js`
    for a file shipped as `.ts`, and a test placed under `projects/smart-todo-app/prototype/`.
    Each is a faithful reading of "File: <original path>" and each fails to resolve.
    """
    fv = {"path": "/repo/src/pkg/clamp.py", "pre": PRE, "post": POST}
    _system, user = build_prompt(fv, "req", language="python", test_name="test_clamp.py")
    assert "ONLY two files" in user
    assert "clamp.py" in user and "test_clamp.py" in user
    assert "nothing else exists in the workspace" in user


def test_a_test_path_from_the_original_repo_is_flattened_beside_the_impl():
    case = reverse_case_from_versions(
        {
            "path": "/repo/prototype/app.js",
            "pre": "export const a=1;\n",
            "post": "export const a=2;\n",
        },
        draft=_draft(),
        chat=_chat_returning(
            {
                "prompt": "The exported value is stale after an update.",
                "test_path": "projects/smart-todo-app/prototype/app.test.js",
                "test_content": "import {test} from 'node:test';\ntest('a',()=>{});\n",
            }
        ),
    )
    paths = [f["path"] for f in case["context"]["files"]]
    assert paths == ["app.js", "app.test.js"], paths


def test_node_builtins_are_satisfiable_with_or_without_the_prefix():
    from aibench.extract.file_versions import unsatisfiable_imports

    src = "const fs=require('fs');const vm=require('vm');import path from 'node:path';"
    assert unsatisfiable_imports("m.js", src) == set()
    assert unsatisfiable_imports("m.js", "const x=require('lodash');") == {"lodash"}


def test_the_shipped_source_path_is_not_the_engineers_home_directory():
    """Every one of 8 built cases carried a real account name and an internal project name."""
    from aibench.extract.sessions import redact_source_path

    out = redact_source_path("/home/li/git/neo-designer/src/access/sdk/coreSdk.ts")
    assert out.startswith("coreSdk.ts#")
    assert "/home/li" not in out and "neo-designer" not in out
    # Distinct files must stay distinct, which is all the full path was ever used for.
    assert redact_source_path("/a/x.py") != redact_source_path("/b/x.py")


def test_machine_paths_in_shipped_source_are_replaced_without_breaking_the_file():
    from aibench.extract.sessions import redact_paths

    src = 'ROOT = "/home/tc/opencode-work/a-first"\n\ndef f():\n    return ROOT\n'
    out = redact_paths(src, path="m.py")
    assert "/home/tc" not in out
    compile(out, "<t>", "exec")


def test_redaction_never_hands_back_source_that_stopped_parsing():
    """The veto only protects files that parsed to begin with.

    A gold file that stops parsing ships as a hard case and corrupts the measurement, so a
    rewrite that breaks one is discarded. A file that never parsed has nothing to protect, and
    withholding the redaction there would leak the path for no gain.
    """
    from aibench.extract.sessions import redact_paths

    good = 'ROOT = "/home/tc/a"\nx = 1\n'
    out = redact_paths(good, path="m.py")
    assert "/home/tc" not in out
    compile(out, "<t>", "exec")

    already_broken = 'x = "/home/tc/a\n'
    assert "/home/tc" not in redact_paths(already_broken, path="m.py")
