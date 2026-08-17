"""The environment a replayer gets is pinned, and the pins are checkable rather than prose.

Every documented version used to live in a comment, and a comment cannot fail. These do.
Every test here fails at `982a9c4`.
"""

from __future__ import annotations

import re
import tomllib

import pytest
import yaml

from aibench.io_util import repo_root

ROOT = repo_root()

#: Everywhere the install command is written down. `uv sync --extra dev` *removes* the grading
#: extra, and `tests/test_grading_env.py` then fails on a promise the manifest makes — so
#: following any of these produced `1 failed, 712 passed` from a clean clone.
_INSTALL_SITES = (
    "README.md",
    "docs/USER_GUIDE.md",
    "docs/REFERENCE.md",
    "docs/html/_src/manual.html",
    "docs/html/manual.html",
    "docs/html/_src/project-overview.html",
    ".github/workflows/ci.yml",
    ".github/workflows/live-smoke.yml",
    "scripts/install-hooks.sh",
    "scripts/run_benchmark.sh",
)

_UV_SYNC = re.compile(r"uv sync(?P<flags>[^\n<&|]*)")


def _invocations(relative: str) -> list[str]:
    """The `uv sync` flag strings a reader would actually run, by file.

    Comment lines are skipped: the fix for this finding *explains itself* in a comment beside
    the corrected command, and a checker that reads the explanation as an invocation would
    report the fix as the bug.
    """
    out: list[str] = []
    for line in (ROOT / relative).read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith(("#", "//", "*")):
            continue
        out.extend(m.group("flags") for m in _UV_SYNC.finditer(line))
    return out


def _pyproject() -> dict:
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))


class TestEveryDocumentedInstallIsTheWorkingOne:
    @pytest.mark.parametrize("relative", _INSTALL_SITES)
    def test_the_command_names_both_extras(self, relative: str):
        found = _invocations(relative)
        assert found, f"{relative}: no `uv sync` invocation found; the check would be vacuous"
        for flags in found:
            assert "--extra grading" in flags, (
                f"{relative}: `uv sync{flags}` prunes the grading extra, which "
                f"configs/grading-env.yaml promises the grader provides"
            )
            assert "--extra dev" in flags, f"{relative}: `uv sync{flags}` omits the dev extra"


class TestTheRuntimesArePinned:
    def test_a_clone_materializes_the_case_directory(self):
        """`.gitignore` exempts `!benchmarks/ai_coding/cases/.gitkeep` and the file was never
        created, so git materialized no directory and `validate-cases` exited 1 on a raw
        `FileNotFoundError`."""
        assert (ROOT / "benchmarks/ai_coding/cases/.gitkeep").is_file()

    def test_node_is_pinned_at_or_above_the_real_floor(self):
        from aibench.languages import MIN_NODE_VERSION

        nvmrc = (ROOT / ".nvmrc").read_text(encoding="utf-8").strip()
        major = int(re.match(r"v?(\d+)", nvmrc).group(1))
        assert major >= MIN_NODE_VERSION[0]

    def test_python_is_pinned_and_the_pin_is_a_version(self):
        pin = (ROOT / ".python-version").read_text(encoding="utf-8").strip()
        assert re.fullmatch(r"\d+\.\d+(\.\d+)?", pin), pin

    def test_ci_installs_the_pinned_node_rather_than_the_runners_own(self):
        ci = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
        assert "actions/setup-node" in ci
        assert "node-version-file: .nvmrc" in ci

    def test_the_opencode_version_is_pinned_in_every_config_that_uses_it(self):
        """`configs/agents/opencode.yaml` recorded "measured against 1.18.15" in a comment, and
        nothing installed, pinned, checked or stamped it — for the adapter the project
        recommends over `tool_loop` for every model comparison."""
        pinned = {}
        for path in sorted((ROOT / "configs/agents").glob("opencode*.yaml")):
            options = yaml.safe_load(path.read_text(encoding="utf-8")).get("options") or {}
            pinned[path.name] = options.get("expected_version")
        assert pinned, "no opencode configs found"
        assert all(pinned.values()), f"unpinned: {[k for k, v in pinned.items() if not v]}"
        assert len(set(pinned.values())) == 1, f"panel rungs disagree on the version: {pinned}"


class TestTheLockIsNotOneMachinesMirror:
    def test_the_project_pins_a_neutral_index(self):
        """`uv.lock` recorded `pypi.tuna.tsinghua.edu.cn` for every package because one
        machine's untracked `~/.config/uv/uv.toml` said so — unreachable outside that network,
        and nothing in the repository said where the packages were supposed to come from."""
        indexes = _pyproject()["tool"]["uv"]["index"]
        assert any(i.get("url", "").startswith("https://pypi.org/") for i in indexes)

    def test_the_lock_agrees_with_it(self):
        lock = (ROOT / "uv.lock").read_text(encoding="utf-8")
        assert "tuna.tsinghua.edu.cn" not in lock
        assert "pypi.org" in lock


class TestTheHooksMatchTheLockAndScanForSecrets:
    def _config(self) -> dict:
        return yaml.safe_load((ROOT / ".pre-commit-config.yaml").read_text(encoding="utf-8"))

    def test_the_ruff_hook_is_the_locked_ruff(self):
        lock = (ROOT / "uv.lock").read_text(encoding="utf-8")
        locked = re.search(r'name = "ruff"\nversion = "([^"]+)"', lock).group(1)
        rev = next(
            r["rev"] for r in self._config()["repos"] if "ruff-pre-commit" in r.get("repo", "")
        )
        assert rev.lstrip("v") == locked, f"hook pins {rev}, lock resolves {locked}"

    def test_a_secrets_hook_exists(self):
        """The `.agent/history-and-reproducibility` plan named this as D5, 唯一防止复发 of the
        incident this repository is recovering from, and it was never landed."""
        entries = [h for r in self._config()["repos"] for h in r.get("hooks", [])]
        assert any("secrets-scan" in str(h.get("entry", "")) for h in entries)

    def test_ci_runs_the_same_scan(self):
        ci = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
        assert "secrets-scan" in ci

    def test_the_scan_reads_the_files_it_is_handed(self, tmp_path):
        from aibench.secrets_scan import scan_paths

        clean = tmp_path / "ok.py"
        clean.write_text("x = 1\n", encoding="utf-8")
        assert scan_paths([clean])["clean"] is True

        dirty = tmp_path / "leak.env"
        dirty.write_text("OPENAI_API_KEY=sk-proj-" + "aB3" * 14 + "\n", encoding="utf-8")
        report = scan_paths([dirty])
        assert report["clean"] is False
        assert report["findings"]


class TestCoverageArtifactsCannotDirtyAManifest:
    def test_gitignore_covers_them(self):
        """An untracked `.coverage` makes `git status --porcelain` non-empty, and
        `provenance.git_revision()` then stamps every later manifest `-dirty`."""
        ignored = (ROOT / ".gitignore").read_text(encoding="utf-8")
        assert ".coverage" in ignored


class TestSkipsAreVisible:
    def test_pytest_reports_them(self):
        """Two test classes skip silently where node is absent, taking the whole JavaScript
        grading path with them while the suite stays green under a bare `-q`."""
        addopts = _pyproject()["tool"]["pytest"]["ini_options"]["addopts"]
        assert "-ra" in addopts


class TestTheDoctorAnswersInsteadOfAComment:
    def test_it_reports_every_external_dependency(self):
        from aibench.preflight import run_checks

        names = {c.name for c in run_checks()}
        assert names == {"python", "node", "grading-env", "opencode", "opencode-sandbox"}

    def test_the_node_check_agrees_with_the_grader(self):
        from aibench.languages import unsupported_node_reason
        from aibench.preflight import check_node

        assert check_node().ok is (unsupported_node_reason() is None)


class TestTheProjectStatesItsLicence:
    def test_pyproject_carries_licence_and_authors(self):
        project = _pyproject()["project"]
        assert project.get("license")
        assert project.get("authors")

    def test_a_licence_file_exists(self):
        assert (ROOT / "LICENSE").is_file()
