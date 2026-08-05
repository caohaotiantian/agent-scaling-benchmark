"""
Session → case draft extraction.

DB connectivity is intentionally pluggable. Provide a SessionRecord export
(JSON) or implement `fetch_sessions` against your warehouse.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class Message:
    role: str
    content: str
    timestamp: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class SessionRecord:
    session_id: str
    messages: list[Message]
    created_at: str | None = None
    language_hints: list[str] = field(default_factory=list)
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    outcome: dict[str, Any] = field(default_factory=dict)
    product: str | None = None


_CODE_INTENT = re.compile(
    r"(实现|修复|重构|bug|fix|implement|refactor|add test|写个|改一下|报错)",
    re.I,
)


def is_coding_session(session: SessionRecord) -> bool:
    text = "\n".join(m.content for m in session.messages if m.role in {"user", "human"})
    return bool(_CODE_INTENT.search(text))


def redact_secrets(text: str) -> str:
    """Replace secret-looking values, without destroying the code around them.

    The value pattern stops at the first quote or whitespace and puts back whatever quoting it
    consumed. Matching ``\\S+`` instead swallowed the closing delimiter, so
    ``assert "token=abc" in url`` became ``assert "token=*** in url`` — an unterminated string
    literal. Measured over ``drafts-from-db``: 56 lines across 90 files in 45 drafts were left
    with an unbalanced quote, and cases built from them shipped with a reference solution and
    a hidden test that could not be parsed.
    """
    out = _SECRET_ASSIGN.sub(_redact_assignment, text)
    out = re.sub(r"sk-[A-Za-z0-9]{10,}", "sk-***", out)
    return re.sub(
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----",
        "***PRIVATE_KEY***",
        out,
    )


#: A secret-looking assignment whose value is either a quoted literal or a bare token.
#:
#: The bare alternative refuses anything that continues into an expression, because `token =
#: os.environ["K"]` names no secret and rewriting it only breaks the file. A bare value behind
#: a colon is held to a higher bar (see :func:`_looks_like_a_secret`) because `token: str` is
#: an ordinary annotation, not a leak.
_SECRET_ASSIGN = re.compile(
    r"""(?ix)
    (api[_-]?key|token|secret|password)   # 1: the name that makes this look like a secret
    (["']?\s*(?P<sep>[:=])\s*)            # 2: the assignment, preserved verbatim; the optional
                                          #    quote closes a quoted *key*, as in {"token": ...}
    (?:
        (?P<q>["'])(?P<quoted>[^"'\n]*)(?P=q)      # a quoted literal, delimiters restored
      | (?P<bare>[A-Za-z0-9_\-]+)(?![\w\-.\[(])    # or a bare token that ends right here
    )
    """
)


def _looks_like_a_secret(value: str) -> bool:
    """Whether a bare value behind a colon is worth redacting.

    ``token: str`` and ``secret: bool`` are type annotations; ``api_key: a1b2c3d4`` is a leak.
    A digit or a length no identifier-shaped type name reaches separates the two.
    """
    return any(c.isdigit() for c in value) or len(value) >= 8


def _redact_assignment(m: re.Match[str]) -> str:
    quote = m.group("q")
    if quote is not None:
        return f"{m.group(1)}{m.group(2)}{quote}***{quote}"
    bare = m.group("bare")
    if m.group("sep") == ":" and not _looks_like_a_secret(bare):
        return m.group(0)
    return f"{m.group(1)}{m.group(2)}***"


def task_fingerprint(prompt: str, file_paths: Iterable[str]) -> str:
    basis = prompt.strip() + "|" + ",".join(sorted(file_paths))
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:16]


def session_to_case_draft(
    session: SessionRecord,
    *,
    task_type: str = "feature",
    language: str = "python",
) -> dict[str, Any] | None:
    """Convert a structured session into a case draft (may need human edit)."""
    if not is_coding_session(session):
        return None

    user_msgs = [m for m in session.messages if m.role in {"user", "human"}]
    if not user_msgs:
        return None

    prompt = redact_secrets(user_msgs[0].content.strip())
    # Prefer last user message as refined intent if multi-turn.
    if len(user_msgs) > 1:
        prompt = redact_secrets(user_msgs[-1].content.strip())

    files: list[dict[str, str]] = []
    for art in session.artifacts:
        if art.get("type") in {"file", "context_file"} and art.get("path") and "content" in art:
            files.append(
                {
                    "path": str(art["path"]),
                    "content": redact_secrets(str(art["content"])),
                }
            )

    if not files:
        # Minimal placeholder so schema can be completed manually.
        files = [{"path": "main.py", "content": "# TODO: restore workspace snapshot\n"}]

    gold_files: list[dict[str, str]] = []
    for art in session.artifacts:
        if art.get("type") in {"accepted_file", "gold_file"} and art.get("accepted"):
            gold_files.append(
                {
                    "path": str(art["path"]),
                    "content": redact_secrets(str(art["content"])),
                }
            )

    grader: dict[str, Any]
    if gold_files:
        grader = {"mode": "gold", "match": "normalized", "gold_files": gold_files}
    else:
        grader = {
            "mode": "script",
            "command": "python -m pytest -q",
        }

    case_id = (
        f"sess-{session.session_id[:12]}-{task_fingerprint(prompt, [f['path'] for f in files])}"
    )
    return {
        "case_id": case_id,
        "schema_version": "0.1",
        "task_type": task_type,
        "language": language,
        "prompt": prompt,
        "context": {
            "files": files,
            "notes": f"draft from session {session.session_id}; review before publish",
        },
        "grader": grader,
        "metadata": {
            "source": "session_derived",
            "source_session_id": session.session_id,
            "difficulty": "medium",
            "tags": ["auto_draft"],
            "split": "dev",
            "fingerprint": task_fingerprint(prompt, [f["path"] for f in files]),
            "outcome": session.outcome,
        },
    }


def filter_and_draft(
    sessions: list[SessionRecord],
    *,
    max_cases: int = 50,
) -> list[dict[str, Any]]:
    drafts: list[dict[str, Any]] = []
    seen_fp: set[str] = set()
    for s in sessions:
        draft = session_to_case_draft(s)
        if not draft:
            continue
        fp = draft["metadata"]["fingerprint"]
        if fp in seen_fp:
            continue
        seen_fp.add(fp)
        drafts.append(draft)
        if len(drafts) >= max_cases:
            break
    return drafts


def load_sessions_from_export(rows: list[dict[str, Any]]) -> list[SessionRecord]:
    """Load sessions from a normalized JSON export list."""
    out: list[SessionRecord] = []
    for row in rows:
        msgs = [
            Message(
                role=m["role"],
                content=m["content"],
                timestamp=m.get("timestamp"),
                metadata=dict(m.get("metadata") or {}),
            )
            for m in row.get("messages") or []
        ]
        out.append(
            SessionRecord(
                session_id=str(row["session_id"]),
                messages=msgs,
                created_at=row.get("created_at"),
                language_hints=list(row.get("language_hints") or []),
                artifacts=list(row.get("artifacts") or []),
                outcome=dict(row.get("outcome") or {}),
                product=row.get("product"),
            )
        )
    return out


def session_record_to_dict(session: SessionRecord) -> dict[str, Any]:
    return asdict(session)
