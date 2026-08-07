"""Keeping the work a run already paid for when the run is killed.

Generation wrote nothing until every draft had been through the model, so a run cut short at
minute ten left an empty directory and billed the same. That happened: a reverse build was
killed by a tool timeout and the whole batch had to be paid for again.
"""

import json

from aibench.checkpoint import JOURNAL_NAME, CaseSink


def _case(cid):
    return {"case_id": cid, "schema_version": "0.1", "metadata": {"tier": "T2"}}


def test_a_case_is_on_disk_before_the_run_finishes(tmp_path):
    sink = CaseSink(tmp_path, max_cases=10)
    assert sink.emit("d1.json", _case("c1")) == "written"
    assert (tmp_path / "c1.json").is_file(), "killed here, the case must already exist"
    assert (tmp_path / JOURNAL_NAME).is_file()


def test_resume_does_not_re_ask_the_model_about_settled_drafts(tmp_path):
    first = CaseSink(tmp_path, max_cases=10)
    first.emit("d1.json", _case("c1"))
    first.note_skip("d2.json", "no_importable_file_versions")

    second = CaseSink(tmp_path, max_cases=10, resume=True)
    assert second.skip_draft("d1.json") and second.skip_draft("d2.json")
    assert not second.skip_draft("d3.json")
    assert second.written == 1 and second.resumed == 1


def test_resume_off_by_default_so_a_fresh_run_stays_fresh(tmp_path):
    CaseSink(tmp_path, max_cases=10).emit("d1.json", _case("c1"))
    assert not CaseSink(tmp_path, max_cases=10).skip_draft("d1.json")


def test_a_deleted_case_is_not_counted_as_written(tmp_path):
    sink = CaseSink(tmp_path, max_cases=10)
    sink.emit("d1.json", _case("c1"))
    (tmp_path / "c1.json").unlink()
    resumed = CaseSink(tmp_path, max_cases=10, resume=True)
    assert resumed.written == 0, "the journal must not outvote the filesystem"


def test_a_torn_final_line_does_not_discard_the_batch(tmp_path):
    sink = CaseSink(tmp_path, max_cases=10)
    sink.emit("d1.json", _case("c1"))
    with (tmp_path / JOURNAL_NAME).open("a", encoding="utf-8") as fh:
        fh.write('{"draft": "d2.json", "sta')  # killed mid-write
    resumed = CaseSink(tmp_path, max_cases=10, resume=True)
    assert resumed.skip_draft("d1.json") and resumed.written == 1


def test_the_cap_still_holds_and_stops_further_paid_work(tmp_path):
    sink = CaseSink(tmp_path, max_cases=2)
    assert sink.emit("d1.json", _case("c1")) == "written"
    assert sink.emit("d2.json", _case("c2")) == "written"
    assert sink.is_full()
    assert sink.emit("d3.json", _case("c3")) == "full"
    assert not (tmp_path / "c3.json").exists()


def test_the_cap_counts_cases_carried_over_from_the_killed_run(tmp_path):
    first = CaseSink(tmp_path, max_cases=3)
    first.emit("d1.json", _case("c1"))
    first.emit("d2.json", _case("c2"))
    resumed = CaseSink(tmp_path, max_cases=3, resume=True)
    assert resumed.emit("d3.json", _case("c3")) == "written"
    assert resumed.emit("d4.json", _case("c4")) == "full", "resume must not exceed --max-cases"


def test_a_repeated_case_id_is_reported_rather_than_silently_overwriting(tmp_path):
    sink = CaseSink(tmp_path, max_cases=10)
    sink.emit("d1.json", _case("c1"))
    assert sink.emit("d2.json", _case("c1")) == "collision"
    assert sink.collisions == ["c1"]
    assert json.loads((tmp_path / "c1.json").read_text())["case_id"] == "c1"


def test_concurrent_writers_never_exceed_the_cap(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    sink = CaseSink(tmp_path, max_cases=5)
    with ThreadPoolExecutor(max_workers=8) as ex:
        list(ex.map(lambda i: sink.emit(f"d{i}.json", _case(f"c{i}")), range(40)))
    assert sink.written == 5
    assert len(list(tmp_path.glob("c*.json"))) == 5


def test_drafts_are_handed_over_as_they_are_built(monkeypatch):
    """extract-from-db held everything in memory and wrote at the end.

    A 4,687-row pull sat at 1.25 GB with nothing on disk; killed there, the whole scan was
    wasted. The callback exists so the caller can persist each draft the moment it exists.
    """
    from aibench.extract import llm_chat_records as m

    records = [object(), object(), object()]
    monkeypatch.setattr(m, "fetch_chat_records", lambda *a, **k: records)
    monkeypatch.setattr(
        m,
        "record_to_case_draft",
        lambda rec: {
            "case_id": f"c{records.index(rec)}",
            "metadata": {"fingerprint": f"f{records.index(rec)}"},
        },
    )
    seen = []
    out = m.extract_case_drafts_from_db("mysql://x", max_cases=10, on_draft=seen.append)
    assert [d["case_id"] for d in seen] == ["c0", "c1", "c2"]
    assert len(out) == 3, "the return value stays a list, so existing callers are unaffected"


def test_the_callback_stops_at_max_cases_like_the_return_value_does(monkeypatch):
    from aibench.extract import llm_chat_records as m

    records = [object() for _ in range(5)]
    monkeypatch.setattr(m, "fetch_chat_records", lambda *a, **k: records)
    monkeypatch.setattr(
        m,
        "record_to_case_draft",
        lambda rec: {
            "case_id": f"c{records.index(rec)}",
            "metadata": {"fingerprint": f"f{records.index(rec)}"},
        },
    )
    seen = []
    m.extract_case_drafts_from_db("mysql://x", max_cases=2, on_draft=seen.append)
    assert len(seen) == 2


def test_an_exhausted_budget_is_not_retried_as_if_it_were_throttling():
    """A gateway reports both "too fast" and "out of money" as HTTP 429.

    Matching on the status code alone retried a permanent failure five times with backoff for
    every draft: 489 retries in one run, 0 cases produced. Worse, the failures then looked
    transient, so --resume would have paid to discover them again.
    """
    from aibench.retry import is_retryable_error

    budget = RuntimeError(
        "Client error '429 Too Many Requests' ... Budget has been exceeded! "
        "Key=00617558 Current cost: 100741139.0, Max budget: 100000000.0"
    )
    assert is_retryable_error(budget) is False
    throttle = RuntimeError("Client error '429 Too Many Requests': rate limit exceeded")
    assert is_retryable_error(throttle) is True
    assert is_retryable_error(RuntimeError("insufficient_quota")) is False
    assert is_retryable_error(RuntimeError("503 Service Unavailable")) is True
