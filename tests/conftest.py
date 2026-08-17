"""Session-wide safety nets for the test suite.

Two things here, both about tests that reach outside their `tmp_path`.

`docs/SESSION-2026-08-14.md` §5.4 records a reviewer running `uv run pytest tests/` inside a
git worktree and finding every tracked directory except `benchmarks/` gone — `src/`, `tests/`,
`scripts/`, `docs/`, `configs/`, `pyproject.toml`. It was never located and never fixed, and
the suite stayed green throughout, because nothing looked. `guard_the_checkout` looks: it
snapshots the repository root before each test and fails the test that removed something. An
unlocated destructive bug becomes a named one the next time it fires.

`require_a_bare_python` turns a confusing failure into a clear one. The graders shell out to
`python -m pytest`, so a `.venv/bin/python -m pytest` invocation with no bare `python` on PATH
produces 17 failures that read as regressions. The project's own journal records that misleading
three people.
"""

from __future__ import annotations

import shutil
from collections.abc import Generator
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

#: Fallback for a checkout with no git — the list as it stood when this was written.
_FALLBACK_ROOT_ENTRIES = (
    "benchmarks",
    "configs",
    "docs",
    "pyproject.toml",
    "README.md",
    "scripts",
    "src",
    "tests",
    "uv.lock",
)


def _tracked_root_entries() -> tuple[str, ...]:
    """Everything at the repository root that git tracks, asked of git rather than listed here.

    Untracked entries (`.venv`, `runs`, `.pytest_cache`) come and go legitimately and are not
    watched. A hardcoded list is watched only until someone commits a new root entry: `LICENSE`,
    `CODEOWNERS`, `.nvmrc` and `.python-version` were all added to this repository after the
    list was written, and none of them was guarded.
    """
    import subprocess

    try:
        out = subprocess.run(
            ["git", "-C", str(ROOT), "ls-files", "-z", "--", ":(top)*"],
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return _FALLBACK_ROOT_ENTRIES
    names = {line.split("/", 1)[0] for line in out.split("\0") if line}
    return tuple(sorted(names)) or _FALLBACK_ROOT_ENTRIES


_TRACKED_ROOT_ENTRIES = _tracked_root_entries()


@pytest.fixture(scope="session", autouse=True)
def require_a_bare_python() -> None:
    if shutil.which("python") is None:
        pytest.fail(
            "no `python` on PATH. Every script grader runs `python -m pytest -q` through the "
            "shell, so without it 17 tests fail in ways that look like regressions. Run the "
            "suite as `uv run pytest tests/ -q`.",
            pytrace=False,
        )


#: Names a real `.env` would set, and which a test reading them must not silently inherit.
#: Derived from `.env.example` rather than listed here, so the two cannot drift. A hand-written
#: list covered 12 of the 18 names that file documents, and the omissions included
#: `AIBENCH_ALLOW_UNSANDBOXED` — the one whose presence changes whether a run is refused.
def _dotenv_names() -> tuple[str, ...]:
    import re

    example = ROOT / ".env.example"
    if not example.is_file():
        return ()
    names = re.findall(r"^#?\s*([A-Z][A-Z0-9_]+)=", example.read_text(encoding="utf-8"), re.M)
    return tuple(sorted(set(names)))


_DOTENV_NAMES = _dotenv_names()


@pytest.fixture(autouse=True)
def do_not_inherit_a_developers_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """A `.env` must not decide what the suite measures.

    `aibench.cli.main` calls `load_dotenv()`, which writes into `os.environ` for the remainder
    of the process — so one test that exercises the CLI hands every test after it the
    developer's gateway, model name and cost rates. The result depends on which tests ran first
    and on whether the machine has a `.env` at all, which is how a suite passes locally and
    fails in CI for reasons nobody can reproduce.

    Cleared, not preserved: a test that needs one of these sets it itself.
    """
    for name in _DOTENV_NAMES:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(autouse=True)
def guard_the_checkout() -> Generator[None, None, None]:
    before = {name for name in _TRACKED_ROOT_ENTRIES if (ROOT / name).exists()}
    yield
    missing = sorted(before - {name for name in before if (ROOT / name).exists()})
    if missing:
        pytest.fail(
            f"this test deleted tracked entries from the checkout: {', '.join(missing)}. "
            "See docs/SESSION-2026-08-14.md §5.4 — a test doing this once emptied a worktree.",
            pytrace=False,
        )
