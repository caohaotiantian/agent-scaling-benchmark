from aibench.extract.history_parse import (
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


def test_coding_record_opencode():
    assert is_coding_record(
        user_agent="opencode/1.2.25",
        tools=["bash", "read"],
        user_text="总结一下设计方案",
    )
