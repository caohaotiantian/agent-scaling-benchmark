"""Promotion is human-gated, and it must not write into the checkout to prove it.

The case-set namespace is redirected at `tmp_path` via `AIBENCH_CASE_ROOT`. This test used to
create `benchmarks/ai_coding/cases/_test_promo_{src,dst}` in the live repository and `rmtree`
them afterwards — the same shape as the unlocated bug that emptied a worktree
(`docs/SESSION-2026-08-14.md` §5.4).
"""

import json
from pathlib import Path

import pytest

from aibench.cases import CASE_ROOT_ENV
from aibench.io_util import write_json
from aibench.promote import promote_cases


@pytest.fixture
def case_root(tmp_path: Path, monkeypatch) -> Path:
    root = tmp_path / "cases"
    root.mkdir()
    monkeypatch.setenv(CASE_ROOT_ENV, str(root))
    return root


def _case(cid: str) -> dict:
    return {
        "case_id": cid,
        "schema_version": "0.1",
        "task_type": "feature",
        "language": "python",
        "prompt": "implement add",
        "context": {
            "files": [
                {"path": "add.py", "content": "def add(a,b):\n    raise NotImplementedError\n"},
                {
                    "path": "test_add.py",
                    "content": "from add import add\n\ndef test_add():\n    assert add(1,2)==3\n",
                },
            ]
        },
        "grader": {"mode": "script", "command": "python -m pytest -q test_add.py"},
        "metadata": {"weak_grader": False, "review_status": "needs_review"},
    }


def test_promote_dry_run_and_script_gate(case_root: Path):
    src = case_root / "_test_promo_src"
    weak = _case("promo-weak")
    weak["grader"] = {"mode": "gold", "key_lines": ["def "]}
    weak["metadata"] = {"weak_grader": True}
    write_json(src / "promo-good.json", _case("promo-good"))
    write_json(src / "promo-weak.json", weak)

    rep = promote_cases(
        source_set="_test_promo_src",
        dest_set="_test_promo_dst",
        require_script=True,
        dry_run=False,
    )
    assert "promo-good" in rep["promoted"]
    assert any(s["case_id"] == "promo-weak" for s in rep["skipped"])

    published = json.loads(
        (case_root / "_test_promo_dst" / "promo-good.json").read_text(encoding="utf-8")
    )
    assert published["metadata"]["review_status"] == "published"


def test_nothing_was_written_into_the_repository(case_root: Path):
    """The point of the redirect, asserted rather than assumed."""
    from aibench.cases import case_set_dir
    from aibench.io_util import repo_root

    resolved = case_set_dir("_test_promo_src")
    assert case_root in resolved.parents
    assert repo_root() / "benchmarks" not in resolved.parents
