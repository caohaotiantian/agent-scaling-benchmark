from aibench.extract.sessions import (
    Message,
    SessionRecord,
    filter_and_draft,
    is_coding_session,
    redact_secrets,
)


def test_redact_secrets():
    s = redact_secrets("api_key=sk-abcdefghijklmnop password=hunter2")
    assert "sk-***" in s or "***" in s
    assert "hunter2" not in s


def test_filter_and_draft():
    sessions = [
        SessionRecord(
            session_id="abc1234567890",
            messages=[
                Message(role="user", content="请帮我实现一个 fizzbuzz 函数"),
                Message(role="assistant", content="好的"),
            ],
            artifacts=[{"type": "file", "path": "fb.py", "content": "def fizzbuzz(n): pass\n"}],
        ),
        SessionRecord(
            session_id="zzz",
            messages=[Message(role="user", content="今天天气怎么样")],
        ),
    ]
    assert is_coding_session(sessions[0]) is True
    assert is_coding_session(sessions[1]) is False
    drafts = filter_and_draft(sessions)
    assert len(drafts) == 1
    assert drafts[0]["metadata"]["source"] == "session_derived"
