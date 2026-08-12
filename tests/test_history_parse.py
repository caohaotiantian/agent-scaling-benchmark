from aibench.extract.history_parse import (
    READ_COMPLETE,
    READ_PARTIAL,
    READ_UNKNOWN,
    extract_files_from_tool_text,
    is_coding_record,
    normalize_messages,
    primary_user_prompt,
)


def test_normalize_and_prompt():
    history = [
        {"role": "system", "content": "You are opencode"},
        {"role": "user", "content": "请实现一个 fizzbuzz 函数"},
        {"role": "assistant", "content": "ok"},
    ]
    msgs = normalize_messages(history)
    assert primary_user_prompt(msgs).startswith("请实现")


def test_path_content_extract():
    text = (
        "<path>C:\\Users\\x\\proj\\stats.py</path>\n"
        "<type>file</type>\n"
        "<content>1: def average(nums):\n2:     return 0\n</content>"
    )
    files = extract_files_from_tool_text(text)
    assert len(files) == 1
    assert files[0]["path"].endswith("stats.py")
    assert "def average" in files[0]["content"]


def _tool_text(path, body):
    return f"<path>{path}</path>\n<type>file</type>\n<content>{body}</content>"


class TestReadFooter:
    """The read tool appends a line describing its own output. It is not part of the file.

    Measured over the 23,868 files reconstructed for the `_rev_raw4` draft pool: 98.0% end in
    one of five such lines, and none of the five ever appears anywhere but the last line. Kept
    as content they made 94.6% of Python files unparseable, so the material was discarded before
    it could become a case.
    """

    def test_a_complete_read_loses_its_footer_and_parses(self):
        body = "def total(items):\n    return sum(items)\n\n(End of file - total 2 lines)"
        (f,) = extract_files_from_tool_text(_tool_text("calc.py", body))
        assert f["content"] == "def total(items):\n    return sum(items)\n"
        assert f["origin"] == READ_COMPLETE

    def test_every_footer_the_corpus_uses_is_recognised(self):
        partial = [
            "(Showing lines 1-2 of 190. Use offset=3 to continue.)",
            "(File has more lines. Use 'offset' parameter to read beyond line 2000)",
            "(Output capped at 50 KB. Showing lines 1-2. Use offset=3 to continue.)",
            "(Output truncated at 51200 bytes. Use 'offset' parameter to read beyond line 2)",
        ]
        for footer in partial:
            (f,) = extract_files_from_tool_text(_tool_text("a.py", f"x = 1\ny = 2\n\n{footer}"))
            assert f["origin"] == READ_PARTIAL, footer
            assert footer not in f["content"], footer

    def test_a_complete_read_footer_that_disagrees_with_the_body_is_partial(self):
        """A read issued with an offset can reach EOF, so the footer says "end of file" about a
        tail. 1,076 of the corpus's 17,010 such footers (6.3%) declare more lines than they
        carry; trusting the wording alone readmits the fragment this predicate exists to reject.
        """
        body = "    return sum(items)\n\n(End of file - total 593 lines)"
        (f,) = extract_files_from_tool_text(_tool_text("calc.py", body))
        assert f["origin"] == READ_PARTIAL

    def test_a_file_without_a_footer_is_left_alone_but_not_vouched_for(self):
        """2.0% of the corpus, and 416 of the 6,021 Python files, carry no footer.

        Calling them complete would be a claim with nothing behind it — and the same text is
        what a `write` call leaves, which is the provenance the whole distinction exists to
        separate out.
        """
        (f,) = extract_files_from_tool_text(_tool_text("a.py", "x = 1\n"))
        assert f["content"] == "x = 1\n"
        assert f["origin"] == READ_UNKNOWN

    def test_a_whole_file_ending_in_blank_lines_is_still_whole(self):
        """The count is compared exactly, so only the tool's own separator may be discounted.

        Collapsing every trailing blank line instead made the check measure how many blank lines
        a file ends with: 61 corpus files were filed as fragments for ending in whitespace.
        """
        file_text = "a = 1\nb = 2\nc = 3\n\n\n"  # 5 lines, the last two blank
        assert len(file_text.splitlines()) == 5
        (f,) = extract_files_from_tool_text(
            _tool_text("a.py", f"{file_text}\n(End of file - total 5 lines)")
        )
        assert f["origin"] == READ_COMPLETE

    def test_a_tail_read_that_lands_one_line_short_is_a_fragment(self):
        """No tolerance in either direction: an `offset` read reaching EOF is the F9 case."""
        (f,) = extract_files_from_tool_text(
            _tool_text("a.py", "b = 2\nc = 3\n\n(End of file - total 3 lines)")
        )
        assert f["origin"] == READ_PARTIAL

    def test_a_footer_above_the_last_line_is_not_one(self):
        """`filesystem.py` in this corpus is a read-tool implementation that builds one of these
        strings on its line 199 of 833. Inspecting only the last non-blank line is what puts it
        out of reach.
        """
        body = 'msg = "(End of file - total 3 lines)"\nprint(msg)\n'
        (f,) = extract_files_from_tool_text(_tool_text("filesystem.py", body))
        assert f["content"] == body

    def test_a_last_line_that_only_starts_with_a_footer_is_not_one(self):
        """What the trailing `$` is for. `re.match` already anchors the start, so a fixture
        whose footer sits mid-line proves nothing — it fails to match either way. The line has
        to *begin* with the footer and carry something after it for the anchor to be load-bearing.
        """
        body = "x = 1\n(End of file - total 3 lines) and more\n"
        (f,) = extract_files_from_tool_text(_tool_text("a.py", body))
        assert f["content"] == body
        assert f["origin"] == READ_UNKNOWN


def test_coding_record_opencode():
    assert is_coding_record(
        user_agent="opencode/1.2.25",
        tools=["bash", "read"],
        user_text="总结一下设计方案",
    )
