"""A shareable bundle must decide provenance by machine, not by memory.

Generated cases land in one directory from two paths that look identical on disk. Measured over
a 575-case build against its own drafts: the 541 LLM-written cases overlap the private drafts by
1.7% of substantive lines, all boilerplate; the 34 that fell back to `heuristic_case_from_draft`
overlap 100%, because that function deep-copies the draft. Those 34 *are* production code.

The overlap gate is separate from the provenance gate because the average hides a tail: among
the LLM cases the median overlap is 0 but the 99th percentile is 0.167, and 36 sit above 5%.
Provenance alone would have shipped those.
"""

import json

import pytest

from aibench.export_bundle import export_bundle, verbatim_share


def _case(cid, *, generation="llm", ok=True, body="def thing():\n    return 0\n"):
    return {
        "case_id": cid,
        "schema_version": "0.1",
        "task_type": "bugfix",
        "language": "python",
        "prompt": "The total is off by one.",
        "context": {
            "files": [
                {"path": "impl.py", "content": body},
                {
                    "path": "test_impl.py",
                    "content": "from impl import thing\n\ndef test_t():\n    assert thing() == 1\n",
                },
            ]
        },
        "grader": {
            "mode": "script",
            "command": "python -m pytest -q",
            "gold_files": [{"path": "impl.py", "content": "def thing():\n    return 1\n"}],
        },
        "metadata": {"generation": generation, "validity_ok": ok, "tier": "T3"},
    }


@pytest.fixture
def sets(tmp_path, monkeypatch):
    root = tmp_path / "benchmarks" / "ai_coding" / "cases"
    (root / "src-set").mkdir(parents=True)
    (root / "drafts").mkdir(parents=True)
    monkeypatch.setattr("aibench.export_bundle.case_set_dir", lambda name: root / name)
    return root


def _write(d, name, case):
    (d / f"{name}.json").write_text(json.dumps(case), encoding="utf-8")


class TestProvenanceGate:
    def test_a_heuristic_case_is_never_exported(self, sets, tmp_path):
        _write(sets / "src-set", "a", _case("a", generation="llm"))
        _write(sets / "src-set", "b", _case("b", generation="heuristic"))
        m = export_bundle(source_set="src-set", output_dir=tmp_path / "out")
        assert m["exported"] == 1
        assert m["rejected"]["provenance"] == 1
        assert m["rejected_ids"]["provenance"] == ["b"]

    def test_a_missing_generation_field_is_refused(self, sets, tmp_path):
        case = _case("a")
        del case["metadata"]["generation"]
        _write(sets / "src-set", "a", case)
        m = export_bundle(source_set="src-set", output_dir=tmp_path / "out")
        assert m["exported"] == 0 and m["rejected"]["provenance"] == 1


class TestVerbatimGate:
    def test_an_llm_case_that_copies_the_draft_is_still_refused(self, sets, tmp_path):
        # Provenance alone would ship this: the average LLM overlap is 1.7%, but the tail is not.
        shared = "\n".join(f"result_value_number_{i} = compute_everything({i})" for i in range(10))
        _write(sets / "drafts", "d", _case("d", body=shared))
        _write(sets / "src-set", "a", _case("a", body=shared))
        m = export_bundle(
            source_set="src-set", output_dir=tmp_path / "out", drafts_dir=sets / "drafts"
        )
        assert m["exported"] == 0
        assert any(k == "verbatim" for k in m["rejected"])

    def test_unrelated_content_does_not_block(self, sets, tmp_path):
        draft_body = "\n".join(
            f"draft_only_symbol_{i} = compute_draft_thing({i})" for i in range(8)
        )
        case_body = "\n".join(f"case_only_symbol_{i} = compute_case_thing({i})" for i in range(8))
        _write(sets / "drafts", "d", _case("d", body=draft_body))
        _write(sets / "src-set", "a", _case("a", body=case_body))
        m = export_bundle(
            source_set="src-set", output_dir=tmp_path / "out", drafts_dir=sets / "drafts"
        )
        assert m["exported"] == 1

    def test_an_empty_drafts_dir_is_an_error_not_a_free_pass(self, sets, tmp_path):
        # Silently indexing nothing would make the gate pass everything.
        _write(sets / "src-set", "a", _case("a"))
        with pytest.raises(ValueError, match="no draft lines"):
            export_bundle(
                source_set="src-set", output_dir=tmp_path / "out", drafts_dir=sets / "drafts"
            )

    def test_share_is_zero_for_a_case_with_no_substantive_lines(self):
        assert verbatim_share({"context": {"files": []}}, {"x"}) == 0.0


class TestOtherGates:
    def test_a_case_failing_audit_is_refused(self, sets, tmp_path):
        _write(sets / "src-set", "a", _case("a", ok=False))
        m = export_bundle(source_set="src-set", output_dir=tmp_path / "out")
        assert m["exported"] == 0 and m["rejected"]["audit"] == 1

    def test_a_case_carrying_a_secret_is_refused(self, sets, tmp_path):
        _write(sets / "src-set", "a", _case("a", body='API_KEY = "sk-abcdefghijklmnop"\n'))
        m = export_bundle(source_set="src-set", output_dir=tmp_path / "out")
        assert m["exported"] == 0 and m["rejected"]["secrets"] == 1


class TestManifest:
    def test_the_manifest_matches_the_files_written(self, sets, tmp_path):
        for i in range(3):
            _write(sets / "src-set", f"a{i}", _case(f"a{i}"))
        _write(sets / "src-set", "h", _case("h", generation="heuristic"))
        out = tmp_path / "out"
        m = export_bundle(source_set="src-set", output_dir=out)
        written = sorted(p.stem for p in out.glob("*.json") if p.name != "MANIFEST.json")
        assert m["exported"] == len(written) == 3
        assert m["case_ids"] == written
        assert json.loads((out / "MANIFEST.json").read_text())["exported"] == 3

    def test_the_manifest_records_why_each_case_was_dropped(self, sets, tmp_path):
        _write(sets / "src-set", "h", _case("h", generation="heuristic"))
        _write(sets / "src-set", "f", _case("f", ok=False))
        m = export_bundle(source_set="src-set", output_dir=tmp_path / "out")
        assert m["rejected_ids"]["provenance"] == ["h"]
        assert m["rejected_ids"]["audit"] == ["f"]
        assert m["considered"] == 2

    def test_a_dry_run_writes_nothing(self, sets, tmp_path):
        _write(sets / "src-set", "a", _case("a"))
        out = tmp_path / "out"
        m = export_bundle(source_set="src-set", output_dir=out, dry_run=True)
        assert m["exported"] == 1
        assert not out.exists()
