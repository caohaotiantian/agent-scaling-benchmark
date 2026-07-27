import json
from pathlib import Path

from aibench.promote import promote_cases
from aibench.io_util import write_json


def test_promote_dry_run_and_script_gate(tmp_path: Path, monkeypatch):
    # point case_set_dir via writing into fixtures path
    root = Path(__file__).resolve().parents[1]
    src = root / "benchmarks/ai_coding/cases" / "_test_promo_src"
    dst_name = "_test_promo_dst"
    dst = root / "benchmarks/ai_coding/cases" / dst_name
    if src.exists():
        import shutil

        shutil.rmtree(src)
    if dst.exists():
        import shutil

        shutil.rmtree(dst)
    src.mkdir(parents=True)

    good = {
        "case_id": "promo-good",
        "schema_version": "0.1",
        "task_type": "feature",
        "language": "python",
        "prompt": "implement add",
        "context": {
            "files": [
                {"path": "add.py", "content": "def add(a,b):\n    raise NotImplementedError\n"},
                {"path": "test_add.py", "content": "from add import add\n\ndef test_add():\n    assert add(1,2)==3\n"},
            ]
        },
        "grader": {"mode": "script", "command": "python -m pytest -q test_add.py"},
        "metadata": {"weak_grader": False, "review_status": "needs_review"},
    }
    weak = dict(good)
    weak["case_id"] = "promo-weak"
    weak["grader"] = {"mode": "gold", "key_lines": ["def "]}
    weak["metadata"] = {"weak_grader": True}
    write_json(src / "promo-good.json", good)
    write_json(src / "promo-weak.json", weak)

    rep = promote_cases(
        source_set="_test_promo_src",
        dest_set=dst_name,
        require_script=True,
        dry_run=False,
    )
    assert "promo-good" in rep["promoted"]
    assert any(s["case_id"] == "promo-weak" for s in rep["skipped"])
    assert (dst / "promo-good.json").is_file()
    published = json.loads((dst / "promo-good.json").read_text())
    assert published["metadata"]["review_status"] == "published"

    import shutil

    shutil.rmtree(src)
    shutil.rmtree(dst)
