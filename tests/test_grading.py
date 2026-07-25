from pathlib import Path

from aibench.cases import load_cases
from aibench.grading import grade_case


def test_gold_key_lines(tmp_path: Path):
    case = next(c for c in load_cases("seed-v0") if c.case_id.endswith("normalize-name"))
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "util.py").write_text(
        "def normalize_name(s):\n    return s.strip().lower()\n",
        encoding="utf-8",
    )
    g = grade_case(case, ws)
    assert g.passed is True
