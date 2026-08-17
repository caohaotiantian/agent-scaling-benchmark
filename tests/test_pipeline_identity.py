"""What the pipeline builds, and whether the thing it measured is the thing it says.

Every test here fails at `982a9c4`.
"""

from __future__ import annotations

import json
import re

import pytest

from aibench.io_util import repo_root

ROOT = repo_root()


def _tool_call(name: str, arguments: dict) -> dict:
    return {
        "role": "assistant",
        "content": "",
        "tool_calls": [{"function": {"name": name, "arguments": json.dumps(arguments)}}],
    }


STUB = "def clamp(x, lo, hi):\n    return x\n"
FIXED = "def clamp(x, lo, hi):\n    return max(lo, min(hi, x))\n"
VISIBLE = "from clamp import clamp\n\n\ndef test_in():\n    assert clamp(5, 0, 9) == 5\n"


def _case(**over):
    base = {
        "case_id": "pipe-clamp",
        "schema_version": "0.1",
        "task_type": "bugfix",
        "language": "python",
        "prompt": "Values outside the requested range are getting through to callers.",
        "context": {
            "files": [
                {"path": "clamp.py", "content": STUB, "role": "impl"},
                {"path": "test_clamp.py", "content": VISIBLE, "role": "test"},
            ]
        },
        "grader": {
            "mode": "script",
            "command": "python -m pytest -q",
            "gold_files": [{"path": "clamp.py", "content": FIXED}],
            "protected_paths": ["test_clamp.py"],
        },
        "metadata": {"generation": "reverse"},
    }
    base.update(over)
    return base


class TestTheOneClickPipelineRunsTheMainLine:
    """B1. `--reverse` is `store_true`, so the *forward* generator — where the model invents the
    defect from a prompt saying "inspired by this real user request (do NOT require the original
    repo)" — was the default, and `grep -rn reverse scripts/e2e_pipeline.sh` returned nothing.
    The one-click pipeline could not even opt in."""

    def _script(self) -> str:
        return (ROOT / "scripts/e2e_pipeline.sh").read_text(encoding="utf-8")

    def test_the_production_path_asks_for_reverse_construction(self):
        body = self._script()
        # The flag has to reach GEN_FLAGS, not merely appear in a comment.
        assert re.search(r"^\s*GEN_FLAGS\+=\(--reverse\)", body, re.M), body[:0] or "no --reverse"

    def test_the_forward_path_survives_behind_a_flag(self):
        """Three intervention experiments judged forward generation ineffective. Reproducing a
        negative result needs the path it was measured on."""
        assert "--forward" in self._script()

    def test_the_dry_run_never_asks_for_it(self):
        """`--reverse` always calls a live model — `cli.py` returns 1 without credentials and
        takes that branch before `--heuristic-only` is read — and the dry-run is what CI runs
        with no secrets."""
        body = self._script()
        dry = body.split('if [[ "$DRY_RUN" -eq 1 ]]; then', 1)[1].split("# Production path", 1)[0]
        assert "--reverse" not in dry

    def test_extraction_asks_for_the_pairs_reverse_construction_needs(self):
        assert "--require-edits" in self._script()


class TestAReverseCaseCarriesATier:
    """B4. With `metadata.tier` falsy, `validity.py` skips `check_tier_invariants` **and** the
    prompt-disclosure verdict — so "the prompt gives the defect away" was never checked on a
    single reverse case."""

    def test_the_label_arms_both_gates_without_reshaping_the_case(self):
        from aibench.extract.tier_shaping import label_tier

        raw = _case()
        before = (
            json.dumps(raw["context"], sort_keys=True),
            json.dumps(raw["grader"], sort_keys=True),
        )
        tier, _notes = label_tier(raw)
        after = (
            json.dumps(raw["context"], sort_keys=True),
            json.dumps(raw["grader"], sort_keys=True),
        )

        assert tier, "two files, symptom-only prompt, no defect marker: this meets a tier"
        assert raw["metadata"]["tier"] == tier
        assert before == after, "labelling must not touch the stub or the reference solution"

    def test_settle_tier_would_have_rewritten_the_stub(self):
        """Why the labeller exists. `shape_for_tier` strips defect markers from `context.files`
        and not from `gold_files`, so the property reverse construction rests on — both sides
        are the real file — would stop holding."""
        from aibench.extract.tier_shaping import settle_tier

        raw = _case()
        raw["context"]["files"][0]["content"] = (
            "def clamp(x, lo, hi):\n    # BUG: ignores hi\n    return x\n"
        )
        settle_tier(raw, "T2")
        assert "# BUG" not in raw["context"]["files"][0]["content"]

    def test_an_unlabellable_case_stays_unlabelled_rather_than_mislabelled(self):
        from aibench.extract.tier_shaping import label_tier

        raw = _case()
        raw["context"]["files"] = [{"path": "a.py", "content": "x = 1\n", "role": "impl"}]
        raw["prompt"] = "The function `clamp` returns the wrong value on line 2; fix line 2."
        tier, notes = label_tier(raw)
        # One file with a disclosing prompt meets T1 only, and T1 allows disclosure.
        assert tier in {"T1", ""}
        assert notes


class TestOneEditIsOneCase:
    """H11 restated. Different rows of a trace session replay the same edit; the model writes a
    different prompt and different tests for each, so `duplicate_fingerprint` sees nothing while
    n is inflated and paired outcomes are correlated. **134 `_rev2026` cases collapse to 66
    unique pairs — 51% redundant.**"""

    def test_the_key_ignores_the_prose_and_reads_the_pair(self):
        from aibench.checkpoint import solution_key

        a, b = _case(), _case(case_id="other")
        b["prompt"] = "Totally different wording for the same defect, at length."
        b["context"]["files"][1]["content"] = VISIBLE.replace("test_in", "test_inside")
        assert solution_key(a) == solution_key(b)

    def test_a_different_defect_keys_differently(self):
        from aibench.checkpoint import solution_key

        a, b = _case(), _case()
        b["grader"]["gold_files"][0]["content"] = FIXED.replace("min(hi, x)", "min(x, hi)")
        assert solution_key(a) != solution_key(b)

    def test_a_case_with_no_reference_solution_cannot_be_deduplicated(self):
        from aibench.checkpoint import solution_key

        raw = _case()
        raw["grader"]["gold_files"] = []
        assert solution_key(raw) is None

    def test_the_sink_drops_the_second_copy_and_says_which(self, tmp_path):
        from aibench.checkpoint import CaseSink

        sink = CaseSink(tmp_path, max_cases=10)
        assert sink.emit("d1.json", _case()) == "written"
        assert sink.emit("d2.json", _case(case_id="pipe-clamp-2")) == "duplicate"
        assert sink.duplicates == ["pipe-clamp-2"]
        assert sorted(p.stem for p in tmp_path.glob("*.json")) == ["pipe-clamp"]

    def test_the_opt_out_keeps_them(self, tmp_path):
        from aibench.checkpoint import CaseSink

        sink = CaseSink(tmp_path, max_cases=10, deduplicate=False)
        assert sink.emit("d1.json", _case()) == "written"
        assert sink.emit("d2.json", _case(case_id="pipe-clamp-2")) == "written"

    def test_a_resumed_run_still_deduplicates(self, tmp_path):
        from aibench.checkpoint import CaseSink

        first = CaseSink(tmp_path, max_cases=10)
        first.emit("d1.json", _case())
        resumed = CaseSink(tmp_path, max_cases=10, resume=True)
        assert resumed.emit("d2.json", _case(case_id="pipe-clamp-2")) == "duplicate"


class TestTheCorpusCannotDriftUnderneathAConfig:
    """RP-17. Every manifest has carried `case_set_fingerprint` and nothing ever checked it, so
    a set whose contents changed produced a number filed against the set it no longer was —
    `_revmixed` reproduces 0 of its 31 recorded fingerprints because the prompts were later
    translated, while still being called `_revmixed` everywhere."""

    def test_a_mismatch_refuses_the_run(self, tmp_path):
        from aibench.io_util import load_yaml
        from aibench.runner import run_benchmark

        cfg = load_yaml(ROOT / "tests/fixtures/configs/runs/baseline.mock.yaml")
        cfg["expected_case_set_fingerprint"] = "v3:notthefingerprint"
        local = tmp_path / "run.yaml"
        local.write_text(json.dumps(cfg), encoding="utf-8")

        with pytest.raises(RuntimeError, match="fingerprint"):
            run_benchmark(run_config_path=local, case_set="seed-v0", output_root=tmp_path)

    def test_the_matching_value_runs_and_is_recorded(self, tmp_path):
        from aibench.cases import load_cases
        from aibench.io_util import load_json, load_yaml
        from aibench.runner import run_benchmark
        from aibench.validity import set_fingerprint

        expected = set_fingerprint(load_cases("seed-v0"))
        cfg = load_yaml(ROOT / "tests/fixtures/configs/runs/baseline.mock.yaml")
        cfg["expected_case_set_fingerprint"] = expected
        local = tmp_path / "run.yaml"
        local.write_text(json.dumps(cfg), encoding="utf-8")

        run_dir = run_benchmark(run_config_path=local, case_set="seed-v0", output_root=tmp_path)
        manifest = load_json(run_dir / "run_manifest.json")
        assert manifest["case_set_fingerprint"] == expected
        assert manifest["expected_case_set_fingerprint"] == expected


class TestAGeneratedCaseSaysWhatMadeIt:
    """RP-18. Cases are LLM-written at temperature 0 with no seed and no pinned generator, from
    a table read `ORDER BY start_time DESC LIMIT n` with `--since`/`--until` defaulting to None.
    Nothing stamped any of it, so every regeneration produced a new fingerprint for reasons
    nobody could name."""

    def test_the_stamp_names_the_generator_and_the_harness(self):
        from aibench.cli import _stamp_generation_provenance

        raw = _case()
        _stamp_generation_provenance(raw, draft_query={"limit": 100}, reverse=True)
        generator = raw["metadata"]["generator"]
        assert set(generator) == {"model", "temperature", "seed", "code_version", "harness_digest"}
        assert generator["harness_digest"]
        assert raw["metadata"]["db_query"] == {"limit": 100}

    def test_the_stamp_lives_where_the_schema_allows_it(self):
        """`case.schema.json` is `additionalProperties: false` everywhere except `metadata`."""
        from aibench.cases import load_schema_validator
        from aibench.cli import _stamp_generation_provenance

        raw = _case()
        _stamp_generation_provenance(raw, draft_query={"limit": 1}, reverse=False)
        assert not list(load_schema_validator().iter_errors(raw))


class TestALaterWriteIsTheFinalState:
    """H1's novel clause. Replay stopped updating `post` once an edit had created the pair, so a
    trace that edited a file and then rewrote it whole shipped the *intermediate* state as the
    reference solution while `pre_origin` still read `read_complete`."""

    def test_a_write_after_an_edit_becomes_the_reference_solution(self):
        from aibench.extract.file_versions import POST_FROM_TOOL_WRITE, replay_file_versions

        body = "def f():\n    return 1\n"
        final = "def f():\n    return 3\n"
        messages = [
            {
                "role": "tool",
                "content": (
                    "<path>impl.py</path>\n<type>file</type>\n<content>"
                    f"{body}\n(End of file - total 2 lines)</content>"
                ),
                "tool_calls": None,
            },
            _tool_call(
                "edit", {"filePath": "impl.py", "oldString": "return 1", "newString": "return 2"}
            ),
            _tool_call("write", {"filePath": "impl.py", "content": final}),
        ]
        versions, _stats = replay_file_versions(messages, require_parse=False)
        assert versions, "the edit should have produced a pair"
        fv = versions[0]
        assert fv.post == final, "the reference solution must be the file as the trace left it"
        assert fv.post_origin == POST_FROM_TOOL_WRITE

    def test_an_edit_only_trace_keeps_its_edit_provenance(self):
        from aibench.extract.file_versions import POST_FROM_EDIT, replay_file_versions

        body = "def f():\n    return 1\n"
        messages = [
            {
                "role": "tool",
                "content": (
                    "<path>impl.py</path>\n<type>file</type>\n<content>"
                    f"{body}\n(End of file - total 2 lines)</content>"
                ),
                "tool_calls": None,
            },
            _tool_call(
                "edit", {"filePath": "impl.py", "oldString": "return 1", "newString": "return 2"}
            ),
        ]
        versions, _stats = replay_file_versions(messages, require_parse=False)
        assert versions[0].post_origin == POST_FROM_EDIT


class TestAMissingCaseSetSaysWhatToDo:
    """B3's surviving clause. `aibench run --case-set auto-v0` in a clone died on a bare
    `FileNotFoundError` naming a path — true and useless, because *no* case set ships with the
    repository and the message gave the reader no way to learn that."""

    def test_the_message_names_a_recovery(self):
        from aibench.cases import iter_case_paths

        with pytest.raises(FileNotFoundError) as excinfo:
            iter_case_paths("no-such-set")
        message = str(excinfo.value)
        assert "generate-cases" in message
        assert "seed-v0" in message
        assert "--dry-run" in message
