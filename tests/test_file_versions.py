"""Reconstructing what a trace actually did to a file.

The defect stops being the model's invention: the stub is the file as the trace found it and
the reference solution is the file as the trace left it. That only works if the reconstructed
"before" is the state the edit was really applied to — a match against the wrong version would
produce a case that is fiction wearing real code.
"""

import json

from aibench.extract.file_versions import (
    PRE_FROM_AMBIGUOUS_PATH,
    PRE_FROM_READ,
    PRE_FROM_TOOL_WRITE,
    PRE_FROM_UNLABELLED_READ,
    replay_file_versions,
)


def _raw_read(path, body):
    return {
        "role": "tool",
        "content": f"<path>{path}</path>\n<type>file</type>\n<content>{body}</content>",
        "tool_calls": None,
    }


def _read(path, body):
    """A read as the tool actually reports it — body, blank line, then its own summary.

    Written this way so these tests exercise footer parsing rather than the no-footer default;
    a suite whose every fixture is footerless proves nothing about the case that matters.
    """
    return _raw_read(path, f"{body}\n(End of file - total {len(body.splitlines())} lines)")


def _window(path, body, *, shown="1-2", total=190):
    """A read that returned only part of the file, as the tool reports it."""
    return _raw_read(path, f"{body}\n(Showing lines {shown} of {total}. Use offset=3 to continue.)")


def _write(path, body):
    return {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {
                "function": {
                    "name": "write",
                    "arguments": json.dumps({"filePath": path, "content": body}),
                }
            }
        ],
    }


def _edit(path, old, new):
    return {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {
                "function": {
                    "name": "edit",
                    "arguments": json.dumps({"filePath": path, "oldString": old, "newString": new}),
                }
            }
        ],
    }


BEFORE = "def total(items):\n    return sum(items) - 1\n"
AFTER = "def total(items):\n    return sum(items)\n"


class TestReplay:
    def test_a_read_then_edit_yields_the_real_before_and_after(self):
        msgs = [_read("app/calc.py", BEFORE), _edit("app/calc.py", "sum(items) - 1", "sum(items)")]
        fvs, st = replay_file_versions(msgs)
        assert len(fvs) == 1
        assert fvs[0].pre == BEFORE
        assert fvs[0].post == AFTER
        assert st.edits_applied == 1

    def test_successive_edits_accumulate_into_one_before_and_after(self):
        msgs = [
            _read("a.py", "x = 1\ny = 2\nz = 3\n"),
            _edit("a.py", "x = 1", "x = 10"),
            _edit("a.py", "z = 3", "z = 30"),
        ]
        fvs, st = replay_file_versions(msgs)
        assert fvs[0].pre == "x = 1\ny = 2\nz = 3\n"
        assert fvs[0].post == "x = 10\ny = 2\nz = 30\n"
        assert fvs[0].edits == 2 and st.edits_applied == 2

    def test_an_edit_the_trace_never_showed_us_is_refused(self):
        # No read, so there is no evidence of what the file looked like. Guessing would invent
        # a "before" that never existed.
        fvs, st = replay_file_versions([_edit("ghost.py", "a", "b")])
        assert fvs == []
        assert st.unlocatable == 1 and st.edits_applied == 0

    def test_an_edit_against_a_stale_version_is_refused(self):
        # `oldString` does not appear in the content the trace read, so the edit belongs to some
        # other version of the file.
        msgs = [_read("a.py", "x = 1\n"), _edit("a.py", "totally_different", "y")]
        fvs, st = replay_file_versions(msgs)
        assert fvs == [] and st.unlocatable == 1

    def test_a_write_replaces_the_working_copy(self):
        msgs = [
            _read("a.py", "old = 1\n"),
            _write("a.py", "fresh = 1\nkeep = 2\n"),
            _edit("a.py", "fresh = 1", "fresh = 99"),
        ]
        fvs, _ = replay_file_versions(msgs)
        assert fvs[0].post == "fresh = 99\nkeep = 2\n"
        # The rewrite is folded into the "defect": `pre` is what the trace read, `post` is what
        # the write left plus the edit. That is not a fix an engineer made to that file.
        assert fvs[0].pre == "old = 1\n"
        assert fvs[0].pre_origin == PRE_FROM_READ

    def test_a_pre_the_trace_never_read_is_labelled_as_the_tool_s_own_writing(self):
        """`pre=original.get(k, base)` falls back to whatever last set `seen[k]`.

        With no read anywhere, that is the model's own first draft — so the "defect" would be a
        typo in code the model just wrote, not an engineer's bug. 182 of the 737 pairs in the
        current draft pool name a file that appears in no read at all.
        """
        msgs = [_write("a.py", "x = 1\ny = 2\n"), _edit("a.py", "x = 1", "x = 99")]
        fvs, _ = replay_file_versions(msgs)
        assert fvs[0].pre == "x = 1\ny = 2\n"
        assert fvs[0].pre_origin == PRE_FROM_TOOL_WRITE

    def test_a_file_edited_back_to_its_original_is_not_a_case(self):
        msgs = [
            _read("a.py", "v = 1\n"),
            _edit("a.py", "v = 1", "v = 2"),
            _edit("a.py", "v = 2", "v = 1"),
        ]
        fvs, _ = replay_file_versions(msgs)
        assert fvs == []


class TestFiltering:
    def test_a_language_the_harness_cannot_run_is_dropped(self):
        # Real traces edit far more C++ than Python; a case in a language we cannot execute
        # fails for every configuration equally, which reads as difficulty.
        msgs = [_read("a.cpp", "int x = 1;\n"), _edit("a.cpp", "int x = 1;", "int x = 2;")]
        fvs, st = replay_file_versions(msgs)
        assert fvs == [] and st.dropped_unregistered == 1

    def test_python_is_kept(self):
        msgs = [_read("a.py", BEFORE), _edit("a.py", "sum(items) - 1", "sum(items)")]
        fvs, _ = replay_file_versions(msgs)
        assert [f.path for f in fvs] == ["a.py"]

    def test_an_unparseable_before_is_dropped(self):
        # Its failure would not be caused by the defect, and the stub gate cannot tell those
        # apart.
        broken = "def f(:\n    pass\n"
        msgs = [_read("a.py", broken), _edit("a.py", "pass", "return 1")]
        fvs, st = replay_file_versions(msgs)
        assert fvs == [] and st.dropped_unparseable == 1

    def test_an_unparseable_after_is_dropped(self):
        msgs = [_read("a.py", "def f():\n    pass\n"), _edit("a.py", "    pass", "  return (")]
        fvs, st = replay_file_versions(msgs)
        assert fvs == [] and st.dropped_unparseable == 1

    def test_filters_can_be_disabled_for_diagnosis(self):
        msgs = [_read("a.cpp", "int x = 1;\n"), _edit("a.cpp", "int x = 1;", "int x = 2;")]
        fvs, _ = replay_file_versions(msgs, languages_only=False, require_parse=False)
        assert len(fvs) == 1


class TestPartialReads:
    """A window of a file is not the file.

    `rev-461e8d91390e3915` shipped in the 19-case clean set with a stub that is the first 40
    lines of a 190-line file, cut mid-expression, against a complete reference solution. It
    passed every validity gate: the task it actually poses is "grow this fragment into the whole
    file", which is not the defect the engineer fixed.
    """

    def test_an_edit_located_only_in_a_window_is_refused(self):
        msgs = [_window("a.py", "x = 1\ny = 2"), _edit("a.py", "x = 1", "x = 99")]
        fvs, st = replay_file_versions(msgs)
        assert fvs == []
        assert st.unlocatable == 1, "honestly unmatched beats matched against a fragment"
        assert st.dropped_partial_read == 1

    def test_a_later_complete_read_rescues_the_file(self):
        msgs = [
            _window("a.py", "x = 1\ny = 2"),
            _read("a.py", "x = 1\ny = 2\nz = 3\n"),
            _edit("a.py", "x = 1", "x = 99"),
        ]
        fvs, _ = replay_file_versions(msgs)
        assert [f.pre for f in fvs] == ["x = 1\ny = 2\nz = 3\n"]
        assert fvs[0].pre_origin == PRE_FROM_READ


class TestProvenance:
    """`pre_origin` is the only thing standing between a case and a fabricated before-state."""

    def test_a_read_after_a_write_shows_the_tool_its_own_output(self):
        msgs = [
            _write("a.py", "x = 1\ny = 2\n"),
            _read("a.py", "x = 1\ny = 2\n"),
            _edit("a.py", "x = 1", "x = 99"),
        ]
        fvs, _ = replay_file_versions(msgs)
        assert fvs[0].pre_origin == PRE_FROM_TOOL_WRITE, (
            "reading a file back after writing it is not evidence of what an engineer found"
        )

    def test_a_read_with_no_footer_is_not_vouched_for(self):
        msgs = [_raw_read("a.py", "x = 1\ny = 2\n"), _edit("a.py", "x = 1", "x = 99")]
        fvs, _ = replay_file_versions(msgs)
        assert fvs[0].pre_origin == PRE_FROM_UNLABELLED_READ

    def test_two_files_sharing_a_basename_withdraw_the_claim(self):
        """Replay keys on basenames, so the read that vouches may have been of the other file."""
        msgs = [
            _read("src/a/conf.py", "x = 1\n"),
            _read("src/b/conf.py", "x = 1\n"),
            _edit("src/b/conf.py", "x = 1", "x = 99"),
        ]
        fvs, _ = replay_file_versions(msgs)
        assert [f.pre_origin for f in fvs] == [PRE_FROM_AMBIGUOUS_PATH]

    def test_provenance_reaches_the_draft(self):
        """`to_dict` is the whole handoff to `iter_file_versions`; dropping the key empties it."""
        msgs = [_read("a.py", "x = 1\n"), _edit("a.py", "x = 1", "x = 99")]
        fvs, _ = replay_file_versions(msgs)
        assert fvs[0].to_dict()["pre_origin"] == PRE_FROM_READ


class TestStats:
    def test_partial_reads_are_reported(self):
        _, st = replay_file_versions([_window("a.py", "x = 1\ny = 2")])
        assert st.to_dict()["dropped_partial_read"] == 1

    def test_yield_is_reported_rather_than_guessed(self):
        msgs = [
            _read("a.py", BEFORE),
            _edit("a.py", "sum(items) - 1", "sum(items)"),
            _edit("b.py", "nope", "x"),
        ]
        _, st = replay_file_versions(msgs)
        assert st.edits_seen == 2
        assert st.edits_applied == 1
        assert st.unlocatable == 1
