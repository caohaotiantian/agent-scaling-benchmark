"""The five Part-B mediums the audit re-verified as holding exactly as filed.

M8, M10, M14, M20, M25. They are not dramatic — each is a number that reads as something it is
not — and they share the property that no gate looked. Every test here fails at `982a9c4`.
"""

from __future__ import annotations

import json

from aibench.io_util import repo_root

ROOT = repo_root()


class TestACaseLostToInfrastructureDoesNotVanish:
    """M8. `aggregate_calibration` builds its case list from the rows that survived the
    `infra_error` filter, so a case whose *every* pass failed on infrastructure has no entry at
    all — not even `incomplete_panel`. `total_cases` then describes a smaller set than the one
    that ran, and nothing says a case was ever attempted."""

    def _runs(self) -> list[dict]:
        return [
            {
                "anchor": "weak",
                "rows": [
                    {"case_id": "ok", "passed": True, "infra_error": False, "tier": "T2"},
                    {"case_id": "lost", "passed": False, "infra_error": True, "tier": "T3"},
                ],
            },
            {
                "anchor": "strong",
                "rows": [
                    {"case_id": "ok", "passed": True, "infra_error": False, "tier": "T2"},
                    {"case_id": "lost", "passed": False, "infra_error": True, "tier": "T3"},
                ],
            },
        ]

    def test_it_is_reported_with_a_reason_of_its_own(self):
        from aibench.calibrate import ALL_ATTEMPTS_INFRA, aggregate_calibration

        report = aggregate_calibration(self._runs())
        by_id = {c["case_id"]: c for c in report["cases"]}
        assert set(by_id) == {"ok", "lost"}
        assert report["total_cases"] == 2
        assert report["all_attempts_infra_count"] == 1

        lost = by_id["lost"]
        assert lost["attempts"] == 0
        assert lost["keep"] is False
        assert any(ALL_ATTEMPTS_INFRA in r for r in lost["reasons"])
        assert lost["tier"] == "T3", "what is known about it is still recorded"

    def test_zero_attempts_is_not_a_difficulty_verdict(self):
        from aibench.calibrate import ALL_ATTEMPTS_INFRA, aggregate_calibration

        lost = next(
            c for c in aggregate_calibration(self._runs())["cases"] if c["case_id"] == "lost"
        )
        assert "not a difficulty verdict" in " ".join(lost["reasons"])
        assert ALL_ATTEMPTS_INFRA != "unsolved_by_all"


class TestARepeatOfTheSameConfigIsNotACandidate:
    """M10. A repeat matrix McNemars every directory against `*-r1`, so two runs of the *same*
    configuration appear as a candidate result — and their discordance is this harness's own
    run-to-run noise: 4.8% of cases over the 17 self-repeat pairs on this corpus, against 14.9%
    over the 53 pairs that moved a knob. `plan-sample-size --from-ablation` averaged the low
    number into the planner, and since required n rises with discordance, that asked for fewer
    cases than the comparison needs."""

    def _rows(self) -> list[dict]:
        manifest = {"main_model": "GLM-5.1", "agent_adapter": "tool_loop"}
        return [
            {
                "experiment_name": "two-glm51-r1",
                "manifest": manifest,
                "case_rows": [
                    {"case_id": "a", "passed": True},
                    {"case_id": "b", "passed": False},
                ],
            },
            {
                "experiment_name": "two-glm51-r2",
                "manifest": dict(manifest),
                "case_rows": [
                    {"case_id": "a", "passed": False},
                    {"case_id": "b", "passed": True},
                ],
            },
            {
                "experiment_name": "two-deepseek-r1",
                "manifest": {**manifest, "main_model": "Deepseek-V4-Flash"},
                "case_rows": [
                    {"case_id": "a", "passed": True},
                    {"case_id": "b", "passed": True},
                ],
            },
        ]

    def _pairwise(self) -> list[dict]:
        from aibench.ablation import compare_runs_pairwise, diff_axes_against_baseline

        rows = self._rows()
        diff_axes_against_baseline(rows, baseline="two-glm51-r1")
        return compare_runs_pairwise(rows, baseline="two-glm51-r1")

    def test_the_self_repeat_is_labelled(self):
        by_name = {p["candidate"]: p for p in self._pairwise()}
        assert by_name["two-glm51-r2"]["self_repeat"] is True
        assert by_name["two-deepseek-r1"]["self_repeat"] is False

    def test_the_planner_excludes_it(self):
        from aibench.stats import observed_discordance

        pairwise = self._pairwise()
        with_noise = observed_discordance(pairwise)
        honest_only = observed_discordance([p for p in pairwise if not p["self_repeat"]])
        assert with_noise == honest_only, "the self-repeat must not move the planning number"

    def test_the_report_says_why_it_is_not_comparable(self):
        from aibench.ablation import _render_ablation_report

        rows = [
            {
                **r,
                "run_id": r["experiment_name"],
                "success_rate": 0.5,
                "total_tokens": 1,
                "run_dir": "-",
            }
            for r in self._rows()
        ]
        report = _render_ablation_report(rows, baseline="two-glm51-r1", pairwise=self._pairwise())
        assert "同一配置的重复运行" in report


class TestEachMatrixRowNamesItselfOnDisk:
    """M14. The matrix row's `experiment_name` never reached `run_benchmark`, so two rows
    sharing `baseline.yaml` both wrote `experiment_name: prod-baseline` into their manifest and
    summary. The ablation summary knew which was which; the artifacts did not."""

    def test_the_manifest_carries_the_row_name(self, tmp_path):
        from aibench.io_util import load_json
        from aibench.runner import run_benchmark

        run_dir = run_benchmark(
            run_config_path=ROOT / "tests/fixtures/configs/runs/baseline.mock.yaml",
            case_set="seed-v0",
            output_root=tmp_path,
            experiment_name="model-ablation-glm51",
        )
        assert load_json(run_dir / "run_manifest.json")["experiment_name"] == "model-ablation-glm51"
        assert load_json(run_dir / "summary.json")["experiment_name"] == "model-ablation-glm51"

    def test_omitting_it_keeps_the_run_config_name(self, tmp_path):
        from aibench.io_util import load_json, load_yaml
        from aibench.runner import run_benchmark

        cfg = load_yaml(ROOT / "tests/fixtures/configs/runs/baseline.mock.yaml")
        run_dir = run_benchmark(
            run_config_path=ROOT / "tests/fixtures/configs/runs/baseline.mock.yaml",
            case_set="seed-v0",
            output_root=tmp_path,
        )
        assert load_json(run_dir / "run_manifest.json")["experiment_name"] == cfg["experiment_name"]


class TestTwoWindowsFilesUnderOneNameStayDistinguishable:
    """M20. `extract_files_from_tool_text` reduces a Windows path to its basename, so two
    genuinely different files under one name collapsed into a single entry and the replay's
    ambiguity check had nothing left to compare — every pair involving the basename compares
    equal, so the read that vouches for `pre` may have been of the other file."""

    def _read(self, path: str, body: str) -> dict:
        return {
            "role": "tool",
            "content": (
                f"<path>{path}</path>\n<type>file</type>\n<content>{body}\n"
                f"(End of file - total {len(body.splitlines())} lines)</content>"
            ),
            "tool_calls": None,
        }

    def _edit(self, path: str, old: str, new: str) -> dict:
        return {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "function": {
                        "name": "edit",
                        "arguments": json.dumps(
                            {"filePath": path, "oldString": old, "newString": new}
                        ),
                    }
                }
            ],
        }

    def test_the_untruncated_path_survives_extraction(self):
        from aibench.extract.history_parse import extract_files_from_tool_text

        (found,) = extract_files_from_tool_text(
            self._read(r"C:\Users\x\proj\calc.py", "x = 1\n")["content"]
        )
        assert found["path"] == "calc.py"
        assert found["source_path"] == r"C:\Users\x\proj\calc.py"

    def test_two_windows_files_sharing_a_name_withdraw_the_claim(self):
        from aibench.extract.file_versions import PRE_FROM_AMBIGUOUS_PATH, replay_file_versions

        messages = [
            self._read(r"C:\proj\a\calc.py", "value = 1\n"),
            self._read(r"C:\proj\b\calc.py", "value = 1\n"),
            self._edit(r"C:\proj\b\calc.py", "value = 1", "value = 2"),
        ]
        versions, _stats = replay_file_versions(messages, require_parse=False)
        assert versions
        assert versions[0].pre_origin == PRE_FROM_AMBIGUOUS_PATH

    def test_one_file_spelled_two_ways_keeps_its_provenance(self):
        from aibench.extract.file_versions import PRE_FROM_READ, replay_file_versions

        messages = [
            self._read(r"C:\proj\a\calc.py", "value = 1\n"),
            self._edit(r"C:\proj\a\calc.py", "value = 1", "value = 2"),
        ]
        versions, _stats = replay_file_versions(messages, require_parse=False)
        assert versions and versions[0].pre_origin == PRE_FROM_READ


class TestSeedV0SaysWhatItIs:
    """M25. `seed-v0` is the only committed case set, and it fails the solvability gate: the CI
    dry-run ablates it with a mock agent carrying hardcoded solutions and `--allow-weak-grader`.
    It is a wiring fixture, and reading it as a benchmark set is the mistake."""

    def test_the_fixture_says_so_in_writing(self):
        note = (ROOT / "tests/fixtures/case_sets/seed-v0/README.md").read_text(encoding="utf-8")
        assert "不是 benchmark" in note or "not a benchmark" in note.lower()

    def test_it_really_does_fail_the_solvability_gate(self):
        """Asserted rather than assumed, so the note above cannot quietly become false."""
        from aibench.cases import load_cases
        from aibench.validity import check_reference_solution

        verdicts = [check_reference_solution(c, case_set="seed-v0") for c in load_cases("seed-v0")]
        assert any(not ok for ok, _ in verdicts), (
            "seed-v0 now passes solvability — if that is intentional, delete this test and the "
            "fixture README's warning together"
        )

    def test_the_dry_run_admits_what_it_is_measuring(self):
        script = (ROOT / "scripts/e2e_pipeline.sh").read_text(encoding="utf-8")
        dry = script.split('if [[ "$DRY_RUN" -eq 1 ]]; then', 1)[1].split("# Production path", 1)[0]
        assert "--allow-weak-grader" in dry
        assert "not production" in dry or "非生产" in dry
