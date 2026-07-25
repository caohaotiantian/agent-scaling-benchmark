"""
Session → case draft extraction.

DB connectivity is intentionally pluggable. Provide a SessionRecord export
(JSON) or implement `fetch_sessions` against your warehouse.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable


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
    patterns = [
        (r"(?i)(api[_-]?key|token|secret|password)\s*[:=]\s*\S+", r"\1=***"),
        (r"sk-[A-Za-z0-9]{10,}", "sk-***"),
        (r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----",
         "***PRIVATE_KEY***"),
    ]
    out = text
    for pat, repl in patterns:
        out = re.sub(pat, repl, out)
    return out


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

    case_id = f"sess-{session.session_id[:12]}-{task_fingerprint(prompt, [f['path'] for f in files])}"
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
