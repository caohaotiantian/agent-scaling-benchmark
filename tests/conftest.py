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

#: Everything at the repository root that git tracks. Untracked entries (`.venv`, `runs`,
#: `.pytest_cache`) come and go legitimately, so they are not watched.
_TRACKED_ROOT_ENTRIES = (
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


@pytest.fixture(scope="session", autouse=True)
def require_a_bare_python() -> None:
    if shutil.which("python") is None:
        pytest.fail(
            "no `python` on PATH. Every script grader runs `python -m pytest -q` through the "
            "shell, so without it 17 tests fail in ways that look like regressions. Run the "
            "suite as `uv run pytest tests/ -q`.",
            pytrace=False,
        )


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
