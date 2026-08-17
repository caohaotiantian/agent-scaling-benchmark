"""Documentation that states a fact the repository can check.

`check_doc_links.py` verifies that a link resolves. It cannot verify that a sentence is true,
and the audit's docs findings are almost all of the second kind: `reference.html §6` asserted the
opposite of `configs/grading-env.yaml`, `§2` contradicted `case.schema.json`, `§5` listed 7 of 13
calibrations. These are the claims that can be checked against their source, so they are.

Every test here fails at `982a9c4`.
"""

from __future__ import annotations

import json
import re

import pytest
import yaml

from aibench.io_util import repo_root

ROOT = repo_root()
SRC = ROOT / "docs/html/_src"
CALIBRATIONS = ROOT / "benchmarks/ai_coding/calibrations"


def _reference() -> str:
    return (SRC / "reference.html").read_text(encoding="utf-8")


class TestTheGradingEnvironmentIsDescribedAsItIs:
    """RP-21. `reference.html §6` — the page `README.md` bills as the authority on 数据格式 and
    门禁规则, and the only page with a 复现环境说明 section — said the grading environment has no
    numpy and filters such cases. `configs/grading-env.yaml` and `grading_env.py` say the
    opposite. A reader concludes the `grading` extra is unnecessary and walks straight into
    RP-09."""

    def test_the_page_names_every_package_the_manifest_promises(self):
        """All of them, not a sample. Checking four of the fourteen is how the page went eight
        names short — `httpx`, `jsonschema`, `openpyxl`, `pymysql`, `pytest`, `rich`,
        `sqlalchemy` and `yaml` are promised by the manifest and were absent from the page,
        with the sentence still reading as if the list were complete."""
        manifest = yaml.safe_load((ROOT / "configs/grading-env.yaml").read_text(encoding="utf-8"))
        promised = set(manifest["python"])
        body = _reference()
        section = body[body.index('id="repro-env"') :]
        missing = sorted(p for p in promised if p not in section)
        assert not missing, (
            f"promised by configs/grading-env.yaml but not named on the page: {missing}"
        )

    def test_the_page_does_not_understate_the_count(self):
        manifest = yaml.safe_load((ROOT / "configs/grading-env.yaml").read_text(encoding="utf-8"))
        body = _reference()
        section = body[body.index('id="repro-env"') :]
        assert f"{len(manifest['python'])} 个第三方包" in section, (
            "the page states a count; it must be the manifest's count"
        )

    def test_the_page_asserts_availability_rather_than_absence(self):
        body = _reference()
        section = body[body.index('id="repro-env"') :]
        assert "评分环境提供" in section
        # The old sentence survives only as history, and must be labelled as such.
        if "未安装 numpy" in section:
            assert "此前" in section, "the old claim is repeated without saying it was wrong"


class TestTheCaseFormatPageMatchesTheSchema:
    """RP-42. §2 contradicted `case.schema.json` on `grader.mode` and on the file roles — and
    the role it omitted was `distractor`, which is the entire mechanism of the T4 tier."""

    def _schema(self) -> dict:
        return json.loads(
            (ROOT / "benchmarks/ai_coding/schemas/case.schema.json").read_text(encoding="utf-8")
        )

    def test_every_grader_mode_is_named(self):
        modes = self._schema()["properties"]["grader"]["properties"]["mode"]["enum"]
        body = _reference()
        for mode in modes:
            assert f"<code>{mode}</code>" in body, f"grader.mode {mode!r} is unlisted"

    def test_every_file_role_is_named(self):
        files = self._schema()["properties"]["context"]["properties"]["files"]
        roles = files["items"]["properties"]["role"]["enum"]
        body = _reference()
        for role in roles:
            assert f"<dt>{role}</dt>" in body, f"context.files[].role {role!r} is unlisted"


class TestEveryPublishedCalibrationIsListed:
    """RP-42's other half: §5 listed 7 of 13, and the missing ones included the file behind the
    site's own headline number."""

    def test_the_page_lists_them_all(self):
        body = _reference()
        published = sorted(p.name for p in CALIBRATIONS.glob("*.json"))
        assert len(published) >= 10
        missing = [name for name in published if name not in body]
        assert not missing, f"reference.html §5 omits {missing}"


class TestTheDocumentedNodeFloorIsTheRealOne:
    """RP-10's documentation half. The page said "≥ 22"; the real floor is 22.18, and below it
    `node --test` exits 0 having discovered nothing — which grades as a pass."""

    def test_the_manual_states_the_floor_the_code_enforces(self):
        from aibench.languages import MIN_NODE_VERSION

        floor = ".".join(str(n) for n in MIN_NODE_VERSION)
        body = (SRC / "manual.html").read_text(encoding="utf-8")
        assert floor in body, f"the manual does not state the {floor} floor"


class TestThePublishedRecomputeRecipeMatchesItsFile:
    """RP-46 and RP-47. The calibrations README paired 105-case band figures with the 126-case
    mean, and two of its per-anchor rates disagreed with the file they cite."""

    def _readme(self) -> str:
        return (CALIBRATIONS / "README.md").read_text(encoding="utf-8")

    def test_the_anchor_rates_match_the_file(self):
        stored = json.loads(
            (CALIBRATIONS / "reverse-v1-hidden1_3anchor_20260808.json").read_text(encoding="utf-8")
        )
        rates: dict[str, list[float]] = {}
        for case in stored["cases"]:
            for anchor, rate in (case.get("by_anchor") or {}).items():
                rates.setdefault(anchor, []).append(rate)
        body = self._readme()
        for anchor in ("weak-single-turn", "strong-tool-loop"):
            values = rates[anchor]
            mean = sum(values) / len(values) * 100
            assert f"{mean:.1f}%" in body, f"{anchor} mean is {mean:.1f}%, not what the README says"

    def test_the_auto_v0_row_states_its_own_denominator(self):
        body = self._readme()
        # The two populations, both named, so neither can be read as the other.
        assert "105" in body and "126" in body
        assert "62.7 / 13.5 / 23.8" in body, "the 126-case figures are not stated anywhere"

    def test_the_pre_v3_fingerprint_files_are_called_out(self):
        """RP-54. Three files carry path-only fingerprints, so their numbers are recomputable
        but their content is not verifiable."""
        body = self._readme()
        assert "v3 之前的口径" in body
        for name in ("auto-v0_3anchor", "disc-v0_2anchor_partial", "retrieval-v0_3anchor"):
            assert name in body


class TestTheReadmeSendsPeopleSomewhereReal:
    """RP-39, RP-40 and RP-44. The README sent newcomers to the older session, at the oldest of
    three `§0.x` blocks, and pointed at the HTML site for content only `REFERENCE.md` has —
    which is the rule the README itself states three lines earlier."""

    def _readme(self) -> str:
        return (ROOT / "README.md").read_text(encoding="utf-8")

    def test_it_names_the_newest_session(self):
        assert "SESSION-2026-08-14" in self._readme()

    def test_it_points_at_the_newest_handoff_block(self):
        assert "§0.-1" in self._readme()

    def test_no_parameter_question_is_routed_to_the_html_site(self):
        body = self._readme()
        for line in body.splitlines():
            if "docs/html/reference.html" in line and "参考手册" in line:
                pytest.fail(f"routes a parameter question at the HTML site: {line.strip()}")

    def test_the_audit_and_the_licence_are_discoverable(self):
        body = self._readme()
        assert "AUDIT-2026-08-17" in body
        assert "LICENSE" in body

    def test_there_is_an_english_entry_point(self):
        """RP-52. Every reader-facing document is Chinese while every identifier is English, and
        nothing carried the three qualifications in a language a non-Chinese reader could use."""
        body = self._readme()
        assert "## English" in body
        english = body.split("## English", 1)[1]
        assert "58 points" in english or "58 point" in english
        assert "anchor panel inverted" in english


class TestTheReferenceManualCoversEverySubcommand:
    """RP-43. `docs/REFERENCE.md` is billed as the parameter-level authority and omitted a whole
    subcommand, five script flags, and 25 of the 40 config files."""

    def _reference_md(self) -> str:
        return (ROOT / "docs/REFERENCE.md").read_text(encoding="utf-8")

    def test_every_cli_subcommand_appears(self):
        cli = (ROOT / "src/aibench/cli.py").read_text(encoding="utf-8")
        names = set(re.findall(r'sub\.add_parser\(\s*\n?\s*"([a-z-]+)"', cli))
        assert len(names) > 10, "the parser shape changed; update this test"
        body = self._reference_md()
        missing = sorted(n for n in names if f"`{n}`" not in body)
        assert not missing, f"REFERENCE.md never documents: {missing}"

    def test_every_config_file_appears(self):
        body = self._reference_md()
        configs = sorted(
            f"{sub}/{p.name}"
            for sub in ("agents", "models", "runs")
            for p in (ROOT / "configs" / sub).glob("*.yaml")
        )
        missing = [c for c in configs if c not in body]
        assert not missing, f"REFERENCE.md §7.1 omits {len(missing)}: {missing[:5]}"

    def test_every_e2e_flag_appears(self):
        script = (ROOT / "scripts/e2e_pipeline.sh").read_text(encoding="utf-8")
        flags = set(re.findall(r"^\s{4}(--[a-z-]+)\)", script, re.M))
        assert len(flags) > 8, "the option parser shape changed; update this test"
        body = self._reference_md()
        missing = sorted(f for f in flags if f"`{f}`" not in body)
        assert not missing, f"REFERENCE.md §9.2 omits {missing}"


class TestTheSiteCarriesItsOwnCaveats:
    """RP-41. The site publishes the retracted headline numbers unqualified while `README.md`
    flags a *different* document as stale."""

    def test_the_overview_repeats_the_three_qualifications(self):
        body = (SRC / "overview.html").read_text(encoding="utf-8")
        head = body[:4000]
        assert "58pp" in head or "58 " in head
        assert "锚点面板强弱反转" in head
        assert "SESSION-2026-08-14" in head


class TestTheShellTemplateDoesNotShipTheWarnedAgainstExample:
    """RP-53. `configs/agents/shell.yaml` carried an `opencode run ...` example that
    `REFERENCE.md §12.3` warns against — the worst place to put a warned-against invocation."""

    def test_no_command_template_example_names_opencode(self):
        body = (ROOT / "configs/agents/shell.yaml").read_text(encoding="utf-8")
        examples = [
            line for line in body.splitlines() if "command_template" in line and "opencode" in line
        ]
        assert not examples, f"still offers opencode as a template: {examples}"
        assert "opencode.yaml" in body, "it should say what to use instead"


class TestRunCitationsAreMarkedAsLocal:
    """RP-22's gate half. Sixteen tracked citations name `runs/*_<timestamp>/` directories that
    a clone never has, and `check_doc_links.py` could not see them: they are code spans rather
    than links, and `runs` is in its `SKIP_DIRS`."""

    def test_the_checker_flags_an_unmarked_citation(self, tmp_path, monkeypatch):
        import importlib.util
        import sys

        spec = importlib.util.spec_from_file_location(
            "_links_probe", ROOT / "scripts/check_doc_links.py"
        )
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        try:
            spec.loader.exec_module(module)
            doc = tmp_path / "d.md"
            doc.write_text("see `runs/ablation_20260814_111227/report.md`\n", encoding="utf-8")
            assert module.check(doc), "an unmarked run citation must be reported"

            doc.write_text(
                "本地产物，未随仓库分发\n\nsee `runs/ablation_20260814_111227/report.md`\n",
                encoding="utf-8",
            )
            assert module.check(doc) == []
        finally:
            sys.modules.pop(spec.name, None)


class TestThePagesQuoteTheRuntimeFloorsTheCodeEnforces:
    """D-04 and D-07's other half. `reference.html` said Node "< 22" while `languages.py` has
    enforced 22.18 since the version gate landed, and the same page's package list was eight
    names short. A documented floor below the real one is the dangerous direction: between the
    two, `node --test` discovers no TypeScript test file and exits 0, which is a pass on the
    defective stub."""

    def test_the_node_floor_on_the_page_is_the_floor_in_the_code(self):
        from aibench.languages import MIN_NODE_VERSION

        floor = ".".join(str(n) for n in MIN_NODE_VERSION)
        section = (body := _reference())[body.index('id="repro-env"') :]
        assert floor in section, f"the page never states the real floor {floor}"


class TestTheManifestFieldTableIsTheManifest:
    """RP-58's documentation half. The code stopped writing `python_executable` and
    `working_directory` — the two fields that carried a home directory — and §16.4 went on
    documenting them, privacy warning included, for fields no manifest carries. Nothing checked
    the table against the thing it describes."""

    def _section(self) -> str:
        body = (ROOT / "docs/REFERENCE.md").read_text(encoding="utf-8")
        start = body.index("### 16.4")
        return body[start : body.index("### 16.5", start)]

    def test_every_recorded_field_is_documented(self):
        from aibench.provenance import environment

        section = self._section()
        missing = sorted(k for k in environment() if f"`{k}`" not in section)
        assert not missing, f"written into every manifest but undocumented: {missing}"

    def test_no_retired_field_is_still_described(self):
        from aibench.provenance import environment

        section = self._section()
        recorded = set(environment())
        stale = [f for f in ("python_executable", "working_directory") if f in recorded]
        assert not stale, (
            "these were retired for carrying a home directory; the code brought them back"
        )
        for field in ("python_executable", "working_directory"):
            if f"`{field}`" in section:
                assert "2026-08-17 起" in section or "此前" in section, (
                    f"§16.4 describes `{field}` as current; no manifest carries it"
                )


class TestTheConfigCountIsCounted:
    """RP-43/RP-55. §7.1 claims to list every file under `configs/` and stated 39; there are 40.
    The omission was `configs/grading-env.yaml` — the manifest that decides which drafts survive
    the import gate — and the pinning test globs only `agents/models/runs`, so it structurally
    could not catch it."""

    def test_the_stated_count_matches_the_directory(self):
        import re

        actual = len(list((ROOT / "configs").rglob("*.yaml")))
        body = (ROOT / "docs/REFERENCE.md").read_text(encoding="utf-8")
        stated = re.search(r"`configs/` 下\*\*全部 (\d+) 个 YAML\*\*", body)
        assert stated, "§7.1 no longer states a count in the form this test reads"
        assert int(stated.group(1)) == actual


class TestTheGraderModeMixIsNotOverstated:
    """RP-42's other half. `reference.html` said the mainline "only produces `script`" while
    4,994 of the 13,307 cases on disk are `gold` — including the committed `seed-v0/case_003`,
    which is in the repository and contradicts the sentence without leaving the checkout."""

    def test_the_page_states_the_measured_mix(self):
        page = _reference()
        assert "<code>gold</code> <strong>4,994</strong>" in page, (
            "the page must state the gold count it used to deny"
        )
        # The old sentence may survive as history, but only if it is labelled as history.
        if "主线只产出" in page:
            assert "此前" in page, "the retracted claim is repeated without saying it was wrong"

    def test_a_committed_fixture_is_gold(self):
        from aibench.cases import load_cases

        modes = {c.case_id: c.grader.mode for c in load_cases("seed-v0", validate=False)}
        assert "gold" in modes.values(), (
            f"the counterexample this test is built on is gone: {modes}"
        )
