"""Reconstructing what a trace actually did to a file.

The defect stops being the model's invention: the stub is the file as the trace found it and
the reference solution is the file as the trace left it. That only works if the reconstructed
"before" is the state the edit was really applied to — a match against the wrong version would
produce a case that is fiction wearing real code.
"""

import json

from aibench.extract.file_versions import replay_file_versions


def _read(path, body):
    return {
        "role": "tool",
        "content": f"<path>{path}</path>\n<type>file</type>\n<content>{body}</content>",
        "tool_calls": None,
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
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "function": {
                            "name": "write",
                            "arguments": json.dumps(
                                {"filePath": "a.py", "content": "fresh = 1\nkeep = 2\n"}
                            ),
                        }
                    }
                ],
            },
            _edit("a.py", "fresh = 1", "fresh = 99"),
        ]
        fvs, _ = replay_file_versions(msgs)
        assert fvs[0].post == "fresh = 99\nkeep = 2\n"

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


class TestStats:
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
