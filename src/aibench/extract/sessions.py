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
    (["']?[ \t]*(?P<sep>[:=])[ \t]*)      # 2: the assignment, preserved verbatim. Horizontal
                                          #    space only: `\s*` let a separator ending one
                                          #    line bind to the docstring opening the next.
                                          #    The optional quote closes a quoted *key*.
    (?:
        # A quoted literal. No whitespace inside, because a value that spans one would be
        # code: in `"…?token=" + tok + "&u="` the quote after `token=` CLOSES a literal, and
        # treating it as an opening one swallowed `+ tok +`. Non-empty, so the first two
        # quotes of a `\"\"\"` opener are never mistaken for an empty value.
        (?P<q>["'])(?P<quoted>[^"'\s]+)(?P=q)
      | (?P<bare>[A-Za-z0-9_\-]+)(?![\w\-.\[(])    # or a bare token that ends right here
    )
    """
)


def _looks_like_a_secret(value: str) -> bool:
    """Whether a bare, unquoted value is worth rewriting.

    Deliberately generous. An unquoted right-hand side is often code rather than a credential —
    ``token = None``, ``password = password or b""``, ``token: SecretStr`` — but guessing which
    from the value alone is hopeless, and guessing conservatively means shipping secrets.
    :func:`redact_source` settles it by evidence instead: a rewrite that breaks the file is
    dropped, so over-reaching here costs nothing on source and catches more everywhere else.
    """
    return len(value) >= 3


def _redact_assignment(m: re.Match[str]) -> str:
    quote = m.group("q")
    if quote is not None:
        return f"{m.group(1)}{m.group(2)}{quote}***{quote}"
    if not _looks_like_a_secret(m.group("bare")):
        return m.group(0)
    return f"{m.group(1)}{m.group(2)}***"


def redact_source(text: str, *, path: str | None = None, language: str | None = None) -> str:
    """Redact file content, but never hand back source that stopped parsing.

    Rewriting code with a regex cannot be made safe in general — the value may be an
    expression, a fragment of a larger literal, or a docstring delimiter — so the guard is
    empirical: if the file parsed before and does not after, the redaction is discarded.

    Leaving a value in place means the secret stays in the case, which ``secrets_scan`` reports
    and ``promote`` refuses to publish. That is the intended trade: a case blocked at the gate
    is recoverable, while a case whose reference solution no longer parses ships as a hard one
    and quietly corrupts the measurement.
    """
    redacted = redact_secrets(text)
    if redacted == text:
        return text
    from aibench.languages import registered_spec, spec_for_path

    spec = spec_for_path(path) if path else None
    if spec is None:
        spec = registered_spec(language)
    if spec is None or not spec.parses(text) or spec.parses(redacted) is not False:
        return redacted

    # The file parsed and the whole rewrite broke it. Rather than discard every redaction over
    # one bad line — which would leave the other secrets in place — keep the line rewrites that
    # survive on their own.
    lines = text.splitlines(keepends=True)
    kept = list(lines)
    for i, line in enumerate(lines):
        rewritten = redact_secrets(line)
        if rewritten == line:
            continue
        trial = [*kept[:i], rewritten, *kept[i + 1 :]]
        if spec.parses("".join(trial)) is not False:
            kept = trial
    return "".join(kept)


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
