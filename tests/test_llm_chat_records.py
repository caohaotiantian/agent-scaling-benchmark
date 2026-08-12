"""What the trace store hands over, and what the draft budget is spent on.

`--max-cases` is a write budget: the loop stops once that many drafts have been appended. Of the
3,312 drafts in the `_rev_raw4` pool, 2,977 carry no before/after pair at all and the rest mostly
cannot show their `pre` came from a complete read — 50 survive, so 98.5% of that budget bought
material that cannot become a reverse-constructed case. `generate-cases --reverse` already refuses to pay a model
for them, but the drafts are written, kept and shipped around regardless.
"""

from aibench.extract import llm_chat_records as m


def _fake_records(monkeypatch, drafts):
    """Serve `drafts` as if each came from one row of the trace store."""
    records = [object() for _ in drafts]
    by_id = dict(zip(records, drafts, strict=True))
    monkeypatch.setattr(m, "fetch_chat_records", lambda *a, **k: records)
    monkeypatch.setattr(m, "record_to_case_draft", by_id.get)
    return records


def _draft(case_id, versions):
    return {
        "case_id": case_id,
        "metadata": {"fingerprint": case_id, "file_versions": versions},
    }


USABLE = [
    {
        "path": "m.py",
        "pre": "def f():\n    return 1\n",
        "post": "def f():\n    return 2\n",
        "pre_origin": "read_complete",
    }
]
#: A pair whose `pre` is what a `write` call put there, not what the trace found.
UNVOUCHED = [
    {"path": "m.py", "pre": "def f():\n    return 1\n", "post": "def f():\n    return 2\n"}
]


class TestRequireUsablePair:
    def test_off_by_default_so_the_forward_generator_keeps_its_input(self, monkeypatch):
        """`extract-from-db` also feeds the route that needs no pairs at all."""
        _fake_records(monkeypatch, [_draft("a", []), _draft("b", USABLE)])
        seen = []
        out = m.extract_case_drafts_from_db("mysql://x", max_cases=10, on_draft=seen.append)
        assert [d["case_id"] for d in out] == ["a", "b"]
        assert [d["case_id"] for d in seen] == ["a", "b"]

    def test_a_draft_with_no_usable_pair_is_not_written(self, monkeypatch):
        _fake_records(
            monkeypatch,
            [_draft("none", []), _draft("unvouched", UNVOUCHED), _draft("good", USABLE)],
        )
        seen = []
        out = m.extract_case_drafts_from_db(
            "mysql://x", max_cases=10, on_draft=seen.append, require_usable_pair=True
        )
        assert [d["case_id"] for d in out] == ["good"]
        assert [d["case_id"] for d in seen] == ["good"], (
            "the callback writes to disk, so it must see the same set the return value reports"
        )

    def test_the_budget_counts_only_what_was_kept(self, monkeypatch):
        """Otherwise `--max-cases 2` stops after two rejects and writes nothing."""
        drafts = [_draft("skip1", []), _draft("keep1", USABLE), _draft("skip2", [])]
        drafts.append(_draft("keep2", USABLE))
        _fake_records(monkeypatch, drafts)
        out = m.extract_case_drafts_from_db("mysql://x", max_cases=2, require_usable_pair=True)
        assert [d["case_id"] for d in out] == ["keep1", "keep2"]
