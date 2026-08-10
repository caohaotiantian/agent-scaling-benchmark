"""`generate-cases` must not lose a case silently, nor spend more than it was asked to.

Both defects surfaced only on a 600-case build. The run reported `generated 600 cases` and
left 575 files, because the filename is the case_id and a repeat overwrote its predecessor.
And `--max-cases` bounded only what was written, so all 810 drafts in the directory were
generated and paid for to keep 600.
"""

import json

from aibench.cli import main


def _draft(cid: str, body: str = "def thing():\n    return 0\n") -> dict:
    return {
        "case_id": cid,
        "schema_version": "0.1",
        "task_type": "bugfix",
        "language": "python",
        "prompt": "Make the suite pass, the total is off by one.",
        "context": {
            "files": [
                {"path": "impl.py", "content": body},
                {
                    "path": "test_impl.py",
                    "content": "from impl import thing\n\ndef test_t():\n    assert thing() == 1\n",
                },
            ]
        },
        "grader": {
            "mode": "script",
            "command": "python -m pytest -q",
            "gold_files": [{"path": "impl.py", "content": "def thing():\n    return 1\n"}],
        },
        "metadata": {"tier": "T2"},
    }


def _run(tmp_path, n_drafts, argv_extra, cid=lambda i: f"draft-{i}"):
    inp, out = tmp_path / "in", tmp_path / "out"
    inp.mkdir()
    for i in range(n_drafts):
        (inp / f"d{i}.json").write_text(json.dumps(_draft(cid(i))), encoding="utf-8")
    rc = main(
        [
            "generate-cases",
            "--input-dir",
            str(inp),
            "--output-dir",
            str(out),
            "--heuristic-only",
            *argv_extra,
        ]
    )
    assert rc == 0
    return out, sorted(p.name for p in out.glob("*.json") if p.name != "_secrets_scan.json")


class TestCostIsBounded:
    def test_only_max_cases_times_oversample_drafts_are_attempted(self, tmp_path, capsys):
        _run(tmp_path, 40, ["--max-cases", "4", "--oversample", "1.5"])
        out = capsys.readouterr().out
        assert "generating from 6 draft(s)" in out, out

    def test_the_default_oversample_is_stated_up_front(self, tmp_path, capsys):
        _run(tmp_path, 40, ["--max-cases", "10"])
        out = capsys.readouterr().out
        assert "generating from 15 draft(s)" in out
        assert "paid generation" in out

    def test_a_small_pool_is_not_padded(self, tmp_path, capsys):
        _run(tmp_path, 3, ["--max-cases", "10"])
        assert "generating from 3 draft(s)" in capsys.readouterr().out


class TestNoCaseIsLostToACollision:
    def test_a_repeated_case_id_is_dropped_and_reported(self, tmp_path, capsys):
        # Every draft claims the same case_id, so the filename collides every time.
        _, files = _run(tmp_path, 5, ["--max-cases", "5"], cid=lambda i: "same-id")
        out = capsys.readouterr().out
        assert len(files) == 1
        assert "dropped" in out and "case_id was already taken" in out

    def test_the_reported_count_matches_the_files_written(self, tmp_path, capsys):
        _, files = _run(tmp_path, 6, ["--max-cases", "6"], cid=lambda i: f"id-{i % 2}")
        out = capsys.readouterr().out
        reported = int(out.split("generated ")[1].split(" cases")[0])
        assert reported == len(files), f"claimed {reported}, wrote {len(files)}"

    def test_distinct_ids_are_all_kept(self, tmp_path):
        _, files = _run(tmp_path, 4, ["--max-cases", "4"])
        assert len(files) == 4
