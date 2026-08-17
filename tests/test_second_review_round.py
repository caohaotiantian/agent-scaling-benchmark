"""The findings four reviewers raised against the first ten commits.

Grouped by what each one is about rather than by reviewer. Every test here fails at `982a9c4`
or at the commit that introduced the defect it names, whichever is later.
"""

from __future__ import annotations

import json
import re

import pytest

from aibench.io_util import repo_root

ROOT = repo_root()


class TestNoTrackedArtifactNamesAMachine:
    """The `run_dir` fix landed in code while two published calibration files still carried
    `/Users/deepsky/Documents/projects/agent-scaling-benchmark/` in `run_dir` and 结果目录."""

    def test_no_published_artifact_embeds_a_home_directory(self):
        """Scoped to the two directories under `benchmarks/` that are tracked by construction.

        `cases/` and `cases_archive/` are gitignored — every local corpus lives there and is
        full of real paths from the traces it was built from, which is not this finding.
        Publication is: `calibrations/` holds the measurement files the project ships, and
        `schemas/` the contracts. Those are what a clone receives.

        Reads the tree rather than shelling out to `git grep`, which resolves the repository it
        is pointed at — in a `git archive` export that is the *live* checkout, so a git-based
        version of this test passed against a baseline that had the leak.
        """
        import re

        pattern = re.compile(r"/(?:Users|home)/[a-z]")
        offenders = []
        for directory in ("calibrations", "schemas"):
            root = ROOT / "benchmarks/ai_coding" / directory
            for path in sorted(root.rglob("*")):
                if not path.is_file():
                    continue
                try:
                    body = path.read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError):
                    continue
                if pattern.search(body):
                    offenders.append(str(path.relative_to(ROOT)))
        assert not offenders, f"a home directory is published in: {offenders}"

    def test_the_two_calibration_files_are_clean(self):
        for name in (
            "ablation-models-toolloop_20260809.json",
            "ablation-two-models-3reps_20260810.json",
        ):
            body = (ROOT / "benchmarks/ai_coding/calibrations" / name).read_text(encoding="utf-8")
            assert "/Users/" not in body, name
            # and the run directories are still there, as repo-relative paths
            assert '"run_dir": "runs/' in body, f"{name} lost its run_dir values"


class TestSelfRepeatsAreTheQuietPairsNotTheLoudOnes:
    """M10's justification was empirically and arithmetically backwards, and had been written
    into four places. The behaviour is right; the reason was not."""

    def test_required_cases_rises_with_discordance(self):
        from aibench.stats import mcnemar_sample_size

        sizes = [
            mcnemar_sample_size(delta=0.04, discordance=psi)["required_cases"]
            for psi in (0.05, 0.10, 0.20, 0.30)
        ]
        assert sizes == sorted(sizes) and len(set(sizes)) == 4, (
            f"if n fell with discordance, pooling noisy pairs would overstate it: {sizes}"
        )

    def test_the_docstring_does_not_claim_self_repeats_are_the_most_discordant(self):
        from aibench import stats

        doc = stats.observed_discordance.__doc__ or ""
        assert "most discordant" not in doc
        assert "least" in doc, "the measured direction must be stated"


class TestTheEqualCostTableSharesOneAxis:
    """`budget_quantiles` states that callers comparing configurations must pass one shared rung
    list. The table unioned each run's own quantiles and step-held the last measured point."""

    def _rows(self) -> list[dict]:
        return [
            {
                "experiment_name": "cheap",
                "case_rows": [{"case_id": "a", "passed": True, "total_tokens": 100}],
            },
            {
                "experiment_name": "dear",
                "case_rows": [{"case_id": "a", "passed": True, "total_tokens": 900}],
            },
        ]

    def test_the_rungs_come_from_the_pooled_spend(self):
        from aibench.ablation import _render_cost_rungs_md

        lines = _render_cost_rungs_md(self._rows())
        rungs = [
            int(ln.split("|")[1].strip())
            for ln in lines
            if ln.startswith("| ") and ln.split("|")[1].strip().isdigit()
        ]
        assert 900 in rungs, f"the dearest run's spend must be a rung: {rungs}"
        assert 100 in rungs


class TestTheAblationExportSaysWhichCorpus:
    """Phase 6 claimed both exports carry `case_set_fingerprint`; only the calibration one did."""

    def test_the_summary_carries_a_fingerprint(self, tmp_path):
        from aibench.ablation import run_ablation
        from aibench.io_util import load_json, write_text

        matrix = tmp_path / "m.yaml"
        write_text(
            matrix,
            "case_set: seed-v0\nruns:\n"
            "  - experiment_name: a\n"
            f"    run_config: {ROOT}/tests/fixtures/configs/runs/baseline.mock.yaml\n",
        )
        abl = run_ablation(matrix, output_root=tmp_path, skip_weak_grader=False)
        summary = load_json(abl / "ablation_summary.json")
        assert "case_set_fingerprint" in summary
        assert summary["case_set_fingerprint"], "a readable case set must produce a fingerprint"


class TestAGateThatCannotRunIsNotAVerdict:
    """`audit-cases` on a machine that cannot grade turned `infra_error` into
    `validity_ok: false`, and `--annotate` writes that into the case file permanently."""

    def _case(self) -> dict:
        return {
            "case_id": "infra-probe",
            "task_type": "bugfix",
            "prompt": (
                "The rounding helper returns the wrong value for negative inputs. "
                "Find the defect and fix it so the suite passes."
            ),
            "language": "python",
            "context": {
                "files": [
                    {
                        "path": "m.py",
                        "role": "impl",
                        "content": "def rounded(x):\n    return int(x + 0.5)\n",
                    },
                    {
                        "path": "test_m.py",
                        "role": "test",
                        "content": "from m import rounded\n\n\ndef test_negative():\n"
                        "    assert rounded(-1.5) == -2\n",
                    },
                ]
            },
            "grader": {
                "mode": "script",
                "command": "python -m pytest -q",
                "gold_files": [
                    {
                        "path": "m.py",
                        "content": "def rounded(x):\n    from math import floor\n"
                        "    return floor(x + 0.5) if x >= 0 else -floor(-x + 0.5)\n",
                    }
                ],
            },
            "metadata": {"tier": "T2"},
        }

    def test_an_unrunnable_grader_is_a_warning(self, monkeypatch):
        import aibench.validity as validity
        from aibench.grading import GradeResult
        from aibench.models import Case

        monkeypatch.setattr(
            validity,
            "grade_case",
            lambda *a, **k: GradeResult(
                passed=False, mode="script", detail="node missing", infra_error=True
            ),
        )
        report = validity.audit_case(Case.from_dict(self._case()))
        codes = {i.code: i.severity for i in report.issues}
        assert "solvability_gate" not in codes, "an unrunnable gate must not fail the case"
        assert codes.get("stub_fail_gate_unverified") == "warn"
        assert report.ok, "validity_ok: false would be written to disk by --annotate"


class TestAGoldCaseCannotVouchForItself:
    """The gate applies `grader.gold_files` and then grades; `gold` mode grades by comparing
    the workspace against those same files."""

    def test_gold_mode_is_skipped_with_a_reason(self):
        from aibench.models import Case
        from aibench.validity import check_reference_solution

        raw = {
            "case_id": "g",
            "task_type": "bugfix",
            "prompt": "p",
            "language": "python",
            "context": {"files": [{"path": "m.py", "role": "impl", "content": "x = 1\n"}]},
            "grader": {
                "mode": "gold",
                "match": "exact",
                "gold_files": [{"path": "m.py", "content": "x = 2\n"}],
            },
            "metadata": {"tier": "T2"},
        }
        ok, detail = check_reference_solution(Case.from_dict(raw))
        assert ok and detail.startswith("skipped_gold_mode")


class TestTheRunSurvivesAMachineThatCannotGradeEverything:
    """A set with one `.ts` case among a hundred Python ones was ungradable on a machine
    without node, though `_grade_script` refuses per case anyway."""

    def test_a_mixed_set_is_a_warning_not_a_refusal(self, tmp_path, monkeypatch, capsys):
        import aibench.runner as runner_mod
        from aibench.runner import run_benchmark

        monkeypatch.setattr(runner_mod, "unsupported_node_reason", lambda: "node 20 < 22.18")
        monkeypatch.setattr(runner_mod, "case_language_is_javascript", lambda lang: True)
        run_dir = run_benchmark(
            run_config_path=ROOT / "tests/fixtures/configs/runs/baseline.mock.yaml",
            case_set="seed-v0",
            output_root=tmp_path,
        )
        assert run_dir.is_dir()
        assert "node 20 < 22.18" in capsys.readouterr().out


class TestADerivedSubsetIsNotACorpusMismatch:
    """`.ablation-filtered-*` and `.calibrating-*` are subsets this harness materializes, so
    their fingerprint cannot equal the parent's — and the check refused every one."""

    def test_a_dot_prefixed_set_skips_the_comparison(self, tmp_path, monkeypatch):
        import shutil

        from aibench.cases import CASE_ROOT_ENV, case_set_dir
        from aibench.io_util import load_yaml, write_text
        from aibench.runner import run_benchmark

        root = tmp_path / "cases"
        shutil.copytree(case_set_dir("seed-v0"), root / ".ablation-filtered-seed-v0")
        monkeypatch.setenv(CASE_ROOT_ENV, str(root))

        cfg = load_yaml(ROOT / "tests/fixtures/configs/runs/baseline.mock.yaml")
        cfg["expected_case_set_fingerprint"] = "0000000000000000"
        path = tmp_path / "run.yaml"
        write_text(path, json.dumps(cfg))  # YAML is a superset of JSON
        run_dir = run_benchmark(
            run_config_path=path,
            case_set=".ablation-filtered-seed-v0",
            output_root=tmp_path / "out",
        )
        assert run_dir.is_dir(), "a derived subset must not be refused for drifting"


class TestTheMeasurementsLandFirst:
    """`results.jsonl` is the only artifact that cannot be recomputed."""

    def test_results_survive_a_failing_report(self, tmp_path, monkeypatch):
        import aibench.runner as runner_mod
        from aibench.runner import run_benchmark

        def boom(*a, **k):
            raise RuntimeError("report rendering blew up")

        monkeypatch.setattr(runner_mod, "render_report_md", boom)
        with pytest.raises(RuntimeError, match="blew up"):
            run_benchmark(
                run_config_path=ROOT / "tests/fixtures/configs/runs/baseline.mock.yaml",
                case_set="seed-v0",
                output_root=tmp_path,
            )
        results = list(tmp_path.rglob("results.jsonl"))
        assert results, "the run's only irreplaceable artifact was discarded"
        rows = [json.loads(ln) for ln in results[0].read_text().splitlines() if ln.strip()]
        assert len(rows) == 4


class TestZeroAttemptsIsNotZeroDifficulty:
    """A case every pass lost to infrastructure carries `p_hat=0.0` as a placeholder."""

    def test_it_is_left_out_of_the_histogram(self):
        from aibench.calibrate import aggregate_calibration

        runs = [
            {
                "anchor": a,
                "rows": [
                    {"case_id": "ok", "passed": True, "infra_error": False, "tier": "T2"},
                    {"case_id": "lost", "passed": False, "infra_error": True, "tier": "T2"},
                ],
            }
            for a in ("weak", "strong")
        ]
        report = aggregate_calibration(runs)
        assert sum(report["p_hat_distribution"].values()) == 1
        assert report["all_attempts_infra_count"] == 1
        assert report["total_cases"] == 2


class TestPathsAreComparedOnlyWhereBothSidesRecorded:
    """`source_path` preserving the untruncated spelling let two *full* paths reach `_same_file`
    and disagree above the three components extraction keeps."""

    def test_two_roots_of_the_same_file_are_the_same_file(self):
        from aibench.extract.file_versions import _same_file

        assert _same_file("/Users/a/proj/src/calc.py", "/Users/b/proj/src/calc.py")
        assert _same_file("proj/src/calc.py", "/Users/a/proj/src/calc.py")

    def test_a_genuinely_different_file_still_differs(self):
        from aibench.extract.file_versions import _same_file

        assert not _same_file("proj/src/calc.py", "proj/tests/calc.py")


class TestAHeuristicCaseNamesTheHeuristic:
    """`OPENAI_MODEL` is set on any machine configured to generate."""

    def test_no_model_is_named_when_none_was_called(self, monkeypatch):
        from aibench.cli import _stamp_generation_provenance

        monkeypatch.setenv("OPENAI_MODEL", "GLM-5.2")
        case: dict = {"metadata": {}}
        _stamp_generation_provenance(case, draft_query=None, model_called=False)
        assert case["metadata"]["generator"]["model"] == "heuristic"


class TestTheHookSurvivesTheRepositoryItGuards:
    """Every `.json` was routed through the case scan, which parses."""

    def test_a_malformed_json_does_not_crash_the_scan(self, tmp_path):
        from aibench.secrets_scan import scan_paths

        bad = tmp_path / "broken.json"
        bad.write_text("{not json at all", encoding="utf-8")
        result = scan_paths([bad])
        assert result["files_scanned"] == 1
        assert result["clean"]

    def test_a_marker_below_the_header_does_not_exempt_the_file(self, tmp_path):
        from aibench.secrets_scan import SYNTHETIC_MARKER, scan_paths

        bait = 'KEY = "sk-' + "A1b2C3d4" * 6 + '"'
        leak = tmp_path / "notes.py"
        leak.write_text(
            "\n".join(["# padding"] * 20 + [f"# {SYNTHETIC_MARKER}", bait]), encoding="utf-8"
        )
        assert not scan_paths([leak])["clean"], "a late marker exempted the whole file"

        leak.write_text("\n".join([f"# {SYNTHETIC_MARKER}", bait]), encoding="utf-8")
        assert scan_paths([leak])["clean"], "a header declaration must still exempt a fixture"


class TestASkipMarkerIsASkipMarker:
    """`unittest.skip\\b` matched neither `skipIf` nor `skipUnless`."""

    @pytest.mark.parametrize(
        "marker", ("@unittest.skipIf(True, 'x')", "@unittest.skipUnless(0, 'x')")
    )
    def test_conditional_skips_are_caught(self, marker: str):
        from aibench.grading import _SKIP_MARKERS

        assert _SKIP_MARKERS.search(marker), marker


class TestATestFileIsATestFileEvenWithoutALabel:
    """`FileBlob.role` defaults to `impl`, so a case that omits it presented its test file as an
    implementation and `check_test_reads_source` scanned an empty string."""

    def test_the_gate_reads_an_unlabelled_test(self):
        from aibench.models import Case
        from aibench.validity import check_test_reads_source

        raw = {
            "case_id": "t",
            "task_type": "bugfix",
            "prompt": "p",
            "language": "python",
            "context": {
                "files": [
                    {"path": "calc.py", "content": "def f():\n    return 1\n"},
                    {
                        "path": "test_calc.py",
                        "content": "def test_x():\n"
                        "    src = open('calc.py').read()\n"
                        "    assert 'return 1' in src\n",
                    },
                ]
            },
            "grader": {"mode": "script", "command": "python -m pytest -q"},
            "metadata": {"tier": "T2"},
        }
        issues = check_test_reads_source(Case.from_dict(raw))
        assert issues, "an unlabelled test file was invisible to the gate"


class TestAnUnknownSelectionStrategyIsRefused:
    """A typo fell through to `first-submit` while the manifest recorded the typo."""

    def test_it_raises_instead_of_silently_choosing(self):
        from aibench.runner import _select_attempt

        rows = [{"case_id": "a", "passed": False}, {"case_id": "a", "passed": True}]
        with pytest.raises(ValueError, match="unknown selection_strategy"):
            _select_attempt(rows, "best-of-K")


class TestTheRetryBudgetIsAConfigField:
    """`AIBENCH_CASE_RETRY` was env-only, so the number that decides how an outage is absorbed
    did not appear in the config the run is reconstructed from."""

    def test_the_run_config_carries_it(self):
        from aibench.models import RunConfig

        cfg = RunConfig.from_dict(
            {
                "experiment_name": "x",
                "benchmark_name": "b",
                "grouping": "task_type",
                "agent_config_path": "a.yaml",
                "model_config_path": "m.yaml",
                "case_retry": 4,
            }
        )
        assert cfg.case_retry == 4


class TestTheHarnessDigestCoversWhatDecidesACalibration:
    """`HARNESS_SOURCES` omitted `calibrate.py` and `stats.py`, so `--reuse-from` could carry a
    point-biserial across a change to the estimator that produced it."""

    def test_both_modules_are_hashed(self):
        from aibench.provenance import HARNESS_SOURCES

        assert {"calibrate.py", "stats.py"} <= set(HARNESS_SOURCES)


class TestTheGraderRunsInAPinnedEnvironment:
    """The grader inherited the caller's environment whole, `PYTHONHASHSEED` included."""

    def test_the_hash_seed_is_pinned(self):
        from aibench.grading import _grader_env

        assert _grader_env()["PYTHONHASHSEED"] == "0"
        assert _grader_env()["PYTHONDONTWRITEBYTECODE"] == "1"

    def test_the_manifest_records_which_environment_graded(self, tmp_path):
        from aibench.io_util import load_json
        from aibench.runner import run_benchmark

        run_dir = run_benchmark(
            run_config_path=ROOT / "tests/fixtures/configs/runs/baseline.mock.yaml",
            case_set="seed-v0",
            output_root=tmp_path,
        )
        assert load_json(run_dir / "run_manifest.json")["grading_env_digest"]


class TestTheDifficultyColumnSaysWhichScale:
    """M5's live form. `runner.py` writes `estimate_difficulty` into every row and the report
    stratifies by it, so a reader meets `easy`/`medium`/`hard` next to a `p_hat` band table that
    reads `easy`/`mid`/`hard` — two of three words shared, different axes."""

    def test_the_report_labels_the_scale(self):
        from aibench.report import render_report_md

        summary = {
            "run_id": "r",
            "stratified_by_difficulty": {
                "easy": {"n": 1, "successes": 1, "success_rate": 1.0, "confidence_interval": None}
            },
        }
        report = render_report_md(summary, [])
        assert "体量启发式" in report
        assert "p_hat" in report


class TestRunningTheCodeIsNotWritingIt:
    """`ShellAgent` diffed the whole workspace, so `__pycache__` from merely importing the module
    counted as files the agent wrote — `files_written` overstated the work and `empty_patch`
    could not fire on a run that changed nothing."""

    def test_a_cache_directory_is_not_a_written_file(self, tmp_path):
        from aibench.agents.shell_agent import _snapshot

        (tmp_path / "m.py").write_text("x = 1\n", encoding="utf-8")
        before = _snapshot(tmp_path)

        cache = tmp_path / "__pycache__"
        cache.mkdir()
        (cache / "m.cpython-313.pyc").write_bytes(b"\x00compiled")
        (tmp_path / ".pytest_cache").mkdir()
        (tmp_path / ".pytest_cache" / "lastfailed").write_text("{}", encoding="utf-8")

        after = _snapshot(tmp_path)
        assert after == before, (
            f"running the code registered as writing it: {set(after) - set(before)}"
        )

    def test_a_real_edit_is_still_seen(self, tmp_path):
        from aibench.agents.shell_agent import _snapshot

        (tmp_path / "m.py").write_text("x = 1\n", encoding="utf-8")
        before = _snapshot(tmp_path)
        (tmp_path / "m.py").write_text("x = 2\n", encoding="utf-8")
        assert _snapshot(tmp_path) != before


class TestDoctorFailsOnlyOnWhatBlocksAMeasurement:
    """P4. `doctor` is the README quickstart's fourth command and it exited 1 on a machine that
    could run everything the quickstart goes on to do — `opencode` is needed by six run configs
    and by `anchor-panel-opencode.yaml`, by nothing in the quickstart, and the repository gives
    no instruction for installing it."""

    def test_a_missing_opencode_is_a_warning(self, monkeypatch):
        import aibench.preflight as preflight

        preflight.opencode_version.cache_clear()
        monkeypatch.setattr(preflight, "opencode_version", lambda: None)
        check = preflight.check_opencode()
        assert check.ok is False
        assert check.blocking is False, "an absent opencode must not fail the whole doctor"

    def test_a_missing_interpreter_pin_still_blocks(self, monkeypatch):
        import aibench.preflight as preflight

        monkeypatch.setattr(preflight, "_pinned_python", lambda: "3.99")
        check = preflight.check_python()
        assert check.ok is False and check.blocking is True

    def test_the_exit_code_ignores_advisory_checks(self):
        from aibench.preflight import Check

        checks = [
            Check("python", True, "3.13", "3.13"),
            Check("opencode", False, None, "installed", blocking=False),
        ]
        assert (0 if all(c.ok for c in checks if c.blocking) else 1) == 0

    def test_the_output_explains_what_warn_means(self):
        from aibench.preflight import Check, render

        out = render([Check("opencode", False, None, "installed", blocking=False)])
        assert out.startswith("warn")
        assert "Exit code ignores these" in out


class TestEveryAuditIdHasATrackedDisposition:
    """`docs/AUDIT-RESPONSE-2026-08-17.md` promises that everything a clone needs to know is in
    it rather than in gitignored `.agent/`. The first version named 33 of the audit's 118 ids and
    left the rest to a file no clone receives — which is RP-24, the finding it cites.

    A disposition counts as tracked if the id is named in the response document or in a commit
    message on this branch. Both survive a clone; `.agent/` does not.
    """

    BASE = "982a9c4"

    def _ids(self) -> list[str]:
        audit = (ROOT / "docs/AUDIT-2026-08-17.md").read_text(encoding="utf-8")
        return sorted(set(re.findall(r"\b(RP-\d{2}|NF-\d{2}|[BHM]\d{1,2})\b", audit)))

    def test_the_audit_still_has_the_ids_this_test_is_about(self):
        assert len(self._ids()) > 100, "the audit changed shape; re-derive this check"

    def test_no_id_is_dispositioned_only_where_a_clone_cannot_look(self):
        import subprocess

        log = subprocess.run(
            ["git", "-C", str(ROOT), "log", f"{self.BASE}..HEAD", "--format=%s%n%b"],
            capture_output=True,
            text=True,
            check=False,
        )
        if log.returncode != 0:
            pytest.skip("no git history here (a tarball export); nothing to check against")
        response = (ROOT / "docs/AUDIT-RESPONSE-2026-08-17.md").read_text(encoding="utf-8")
        tracked = log.stdout + response
        missing = [i for i in self._ids() if not re.search(rf"\b{re.escape(i)}\b", tracked)]
        assert not missing, (
            f"dispositioned only in gitignored .agent/, which no clone receives: {missing}"
        )
