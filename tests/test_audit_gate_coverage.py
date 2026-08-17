"""Gates that were armed but not looking, and gates that were looking at the wrong thing.

Every test here fails at `982a9c4`.
"""

from __future__ import annotations

import io
import json
import tarfile
from pathlib import Path

import pytest

from aibench.compose import compose_case, donor_files
from aibench.export_bundle import _substantive_lines, export_bundle, verbatim_share
from aibench.io_util import repo_root, write_json
from aibench.models import Case
from aibench.secrets_scan import scan_text
from aibench.validity import case_fingerprint

STUB = "def clamp(x, lo, hi):\n    return x\n"
FIXED = "def clamp(x, lo, hi):\n    return max(lo, min(hi, x))\n"


def _case_dict(**overrides):
    base = {
        "case_id": "gate-clamp",
        "schema_version": "0.1",
        "task_type": "bugfix",
        "language": "python",
        "prompt": "Values outside the range are getting through. Fix it.",
        "context": {
            "files": [
                {"path": "clamp.py", "content": STUB, "role": "impl"},
                {
                    "path": "test_clamp.py",
                    "content": "from clamp import clamp\n\n\ndef test_in():\n"
                    "    assert clamp(5, 0, 9) == 5\n",
                    "role": "test",
                },
            ]
        },
        "grader": {
            "mode": "script",
            "command": "python -m pytest -q",
            "gold_files": [{"path": "clamp.py", "content": FIXED}],
        },
        "metadata": {"generation": "llm", "validity_ok": True},
    }
    base.update(overrides)
    return base


class TestTheExportGateSeesWhatItShips:
    """NF-03. `_substantive_lines` read only `context.files`, while `write_json` ships
    `gold_files` and `hidden_tests` in full. Measured on `_clean2026`: 3,890 lines seen against
    5,659 never looked at, so `verbatim_share` described 41% of the shipped source."""

    _LONG = "    result_value = compute_the_thing(alpha, beta, gamma, delta)"

    def test_gold_and_hidden_lines_are_counted(self):
        case = _case_dict()
        case["grader"]["gold_files"] = [{"path": "clamp.py", "content": self._LONG + "\n"}]
        case["grader"]["hidden_tests"] = [{"path": "clamp_spec.py", "content": self._LONG + "2\n"}]
        lines = _substantive_lines(case)
        assert self._LONG.strip() in lines
        assert (self._LONG + "2").strip() in lines

    def test_a_case_whose_leak_is_only_in_its_gold_file_is_no_longer_invisible(self):
        case = _case_dict()
        case["context"]["files"] = [{"path": "a.py", "content": "x = 1\n"}]
        case["grader"]["gold_files"] = [{"path": "a.py", "content": self._LONG + "\n"}]
        assert verbatim_share(case, {self._LONG.strip()}) == 1.0


class TestTheExportGateHasTheEscapePromoteAlreadyHad:
    """RP-05's code half. Five gates bear on a bundle; the secrets one was the only one with no
    escape, while the same gate on `promote` has had `--allow-secrets` all along."""

    def _set(self, tmp_path: Path, **case_overrides) -> str:
        name = "_test_export_gate"
        directory = repo_root() / "benchmarks/ai_coding/cases" / name
        for stale in directory.glob("*.json"):
            stale.unlink()
        write_json(directory / "c.json", _case_dict(**case_overrides))
        return name

    @pytest.fixture(autouse=True)
    def _cleanup(self):
        yield
        directory = repo_root() / "benchmarks/ai_coding/cases/_test_export_gate"
        if directory.is_dir():
            for stale in directory.glob("*.json"):
                stale.unlink()
            directory.rmdir()

    def test_a_secret_still_blocks_by_default(self, tmp_path):
        case = _case_dict()
        case["context"]["files"][0]["content"] = 'KEY = "sk-proj-' + "A" * 40 + '"\n'
        name = self._set(tmp_path, **{k: v for k, v in case.items() if k != "case_id"})
        manifest = export_bundle(source_set=name, output_dir=tmp_path / "out", dry_run=True)
        assert manifest["rejected"].get("secrets") == 1

    def test_allow_secrets_lets_it_through_and_says_so(self, tmp_path):
        case = _case_dict()
        case["context"]["files"][0]["content"] = 'KEY = "sk-proj-' + "A" * 40 + '"\n'
        name = self._set(tmp_path, **{k: v for k, v in case.items() if k != "case_id"})
        manifest = export_bundle(
            source_set=name, output_dir=tmp_path / "out", dry_run=True, allow_secrets=True
        )
        assert manifest["exported"] == 1
        assert "acknowledged" in manifest["gates"]["secrets"]

    def test_review_choice_is_refused_unless_asked_for(self, tmp_path):
        name = self._set(tmp_path, metadata={"generation": "review-choice", "validity_ok": True})
        blocked = export_bundle(source_set=name, output_dir=tmp_path / "out", dry_run=True)
        assert blocked["rejected"].get("provenance") == 1
        allowed = export_bundle(
            source_set=name, output_dir=tmp_path / "out", dry_run=True, allow_review_choice=True
        )
        assert allowed["exported"] == 1


class TestAKeywordArgumentIsInterface:
    """H2. `check_hidden_tests_are_inferable` read imports and attribute accesses but not
    keyword argument names, so `rev-05e88429bf55fa4d` shipped `validity_ok: true` while every
    run of it died on `TypeError: unexpected keyword argument 'extra_skip'`."""

    def _case(self, hidden: str) -> Case:
        raw = _case_dict()
        raw["context"]["files"][0] = {
            "path": "cfg.py",
            "content": "def discover(root, extra_dirs=None):\n    return []\n",
            "role": "impl",
        }
        raw["grader"]["hidden_tests"] = [{"path": "cfg_spec.py", "content": hidden}]
        return Case.from_dict(raw)

    def test_an_unknowable_kwarg_is_an_error(self):
        from aibench.validity import check_hidden_tests_are_inferable

        issues = check_hidden_tests_are_inferable(
            self._case("import cfg\n\n\ndef test_x():\n    cfg.discover('.', extra_skip='v')\n")
        )
        assert [i.code for i in issues] == ["hidden_test_requires_unknowable_kwarg"]
        assert "extra_skip" in issues[0].message

    def test_a_kwarg_the_stub_declares_is_fine(self):
        from aibench.validity import check_hidden_tests_are_inferable

        issues = check_hidden_tests_are_inferable(
            self._case("import cfg\n\n\ndef test_x():\n    cfg.discover('.', extra_dirs=['a'])\n")
        )
        assert issues == []

    def test_a_standard_library_kwarg_in_a_fixture_is_not_the_cases_interface(self):
        from aibench.validity import check_hidden_tests_are_inferable

        issues = check_hidden_tests_are_inferable(
            self._case(
                "import os\nimport cfg\n\n\ndef test_x(tmp_path):\n"
                "    os.makedirs(tmp_path / 'a', exist_ok=True)\n"
                "    cfg.discover(tmp_path)\n"
            )
        )
        assert issues == []

    def test_the_diagnostic_script_and_the_gate_share_one_implementation(self):
        """The checker existed in `scripts/discrimination_diagnostic.py` and was wired to
        nothing. One copy, or the script and the gate drift and only one of them is enforced."""
        import importlib.util
        import sys

        from aibench import validity

        path = repo_root() / "scripts/discrimination_diagnostic.py"
        spec = importlib.util.spec_from_file_location("_diag_probe", path)
        assert spec and spec.loader
        diag = importlib.util.module_from_spec(spec)
        # Registered before exec: the module defines dataclasses, and `dataclasses` resolves
        # their annotations through `sys.modules[cls.__module__]`.
        sys.modules[spec.name] = diag
        try:
            spec.loader.exec_module(diag)
            assert diag.hidden_call_keywords is validity.hidden_call_keywords
        finally:
            sys.modules.pop(spec.name, None)


class TestDonorsAreNotTests:
    """NF-06. `role` is optional and defaults to `impl`, and every file in all four committed
    `seed-v0` cases omits it — `test_fizzbuzz.py` included."""

    def test_an_unlabelled_test_file_is_never_donated(self):
        seed = repo_root() / "tests/fixtures/case_sets/seed-v0/case_001_fizzbuzz.json"
        case = json.loads(seed.read_text(encoding="utf-8"))
        assert all(f.get("role") is None for f in case["context"]["files"]), "fixture changed"
        assert [f["path"] for f in donor_files(case)] == ["fizzbuzz.py"]

    def test_composition_recomputes_the_identity_it_changed(self):
        host = _case_dict()
        host["metadata"]["fingerprint"] = case_fingerprint(host)
        donor = _case_dict(case_id="donor")
        donor["context"]["files"] = [
            {"path": "other.py", "content": "def other():\n    return 2\n", "role": "impl"}
        ]
        out = compose_case(host, [donor], target_files=6)
        assert out["metadata"]["distractors_added"] == 1
        assert out["metadata"]["fingerprint"] != host["metadata"]["fingerprint"]
        assert out["metadata"]["fingerprint"] == case_fingerprint(out)


class TestInvalidCasesLeaveTheDenominator:
    """H5. `audit-cases` writes `validity_ok` back and nothing on the run path read it, so
    64 of 133 `_rev2026` cases sat in the denominator of every ablation."""

    def test_a_failed_audit_is_excluded(self, tmp_path):
        from aibench.ablation import _filter_unusable_cases

        name = "_test_invalid_filter"
        directory = repo_root() / "benchmarks/ai_coding/cases" / name
        try:
            write_json(directory / "ok.json", _case_dict(case_id="ok"))
            bad = _case_dict(case_id="bad")
            bad["metadata"] = {"generation": "llm", "validity_ok": False}
            write_json(directory / "bad.json", bad)

            resolved, counts = _filter_unusable_cases(name, skip_weak=True, skip_invalid=True)
            assert counts["validity_failed"] == 1
            kept = sorted(
                p.stem
                for p in (repo_root() / "benchmarks/ai_coding/cases" / resolved).glob("*.json")
            )
            assert kept == ["ok"]
        finally:
            for d in (
                directory,
                repo_root() / f"benchmarks/ai_coding/cases/.ablation-filtered-{name}",
            ):
                if d.is_dir():
                    for f in d.glob("*.json"):
                        f.unlink()
                    d.rmdir()

    def test_an_unaudited_case_is_not_treated_as_failed(self, tmp_path):
        from aibench.ablation import _filter_unusable_cases

        name = "_test_unaudited_filter"
        directory = repo_root() / "benchmarks/ai_coding/cases" / name
        try:
            unaudited = _case_dict(case_id="unaudited")
            unaudited["metadata"] = {"generation": "llm"}
            write_json(directory / "unaudited.json", unaudited)
            resolved, counts = _filter_unusable_cases(name, skip_weak=True, skip_invalid=True)
            assert counts == {"weak_grader": 0, "validity_failed": 0}
            assert resolved == name
        finally:
            if directory.is_dir():
                for f in directory.glob("*.json"):
                    f.unlink()
                directory.rmdir()


class TestAMostlyBrokenRunIsFlagged:
    """H8's residual. The guard was `if not effective_case_count`, so it fired only at exactly
    zero — 1 effective case out of 167 got a full-weight rate and no warning."""

    def test_one_effective_case_out_of_many_is_warned_about(self):
        from aibench.ablation import _infra_dominated

        assert _infra_dominated({"case_count": 167, "effective_case_count": 1}) is True
        assert _infra_dominated({"case_count": 167, "effective_case_count": 0}) is True
        assert _infra_dominated({"case_count": 10, "effective_case_count": 10}) is False


class TestArchivesCannotLinkOutOfTheWorkspace:
    """H15's surviving tar clause. Member paths were checked; link *targets* were not, and
    Python only started refusing that by default in 3.14."""

    def test_a_symlink_escaping_the_workspace_is_refused(self, tmp_path):
        from aibench.workspace import _safe_extract_tar

        archive = tmp_path / "s.tar"
        with tarfile.open(archive, "w") as tf:
            link = tarfile.TarInfo("escape")
            link.type = tarfile.SYMTYPE
            link.linkname = "../../outside"
            tf.addfile(link)
            payload = tarfile.TarInfo("payload.txt")
            data = b"x"
            payload.size = len(data)
            tf.addfile(payload, io.BytesIO(data))

        dest = tmp_path / "ws"
        dest.mkdir()
        with tarfile.open(archive) as tf, pytest.raises(RuntimeError, match="links outside"):
            _safe_extract_tar(tf, dest)


def test_a_project_scoped_openai_key_is_recognised():
    """H15's third surviving clause: `sk-proj-` stops `openai_sk`'s character class four
    characters short of its length floor, so the current key format matched nothing."""
    findings = scan_text("OPENAI_API_KEY=sk-proj-" + "aB3" * 14, path="x")
    assert "openai_project_key" in {f.rule for f in findings}
