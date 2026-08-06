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


def redact_secrets(text: str, *, aggressive: bool = False) -> str:
    """Replace secret-looking values, without destroying the code around them.

    The value pattern stops at the first quote or whitespace and puts back whatever quoting it
    consumed. Matching ``\\S+`` instead swallowed the closing delimiter, so
    ``assert "token=abc" in url`` became ``assert "token=*** in url`` — an unterminated string
    literal. Measured over ``drafts-from-db``: 56 lines across 90 files in 45 drafts were left
    with an unbalanced quote, and cases built from them shipped with a reference solution and
    a hidden test that could not be parsed.

    ``aggressive`` widens what counts as a secret value. It is only safe where a parse check
    can veto the result, so it defaults off and :func:`redact_source` turns it on for exactly
    the inputs it can verify.
    """
    return _redact_spanning(_redact_line_local(text, aggressive=aggressive))


def _redact_line_local(text: str, *, aggressive: bool) -> str:
    """The rewrites that act within one line — the only ones that can break syntax."""
    out = _SECRET_ASSIGN.sub(lambda m: _redact_assignment(m, aggressive=aggressive), text)
    return re.sub(r"sk-[A-Za-z0-9]{10,}", "sk-***", out)


def _redact_spanning(text: str) -> str:
    """Rewrites that legitimately cross lines. Applied whole-text, still subject to the veto.

    Kept out of the line-by-line salvage because it cannot match a single line, which is how a
    file with an embedded private key came back byte-identical whenever any other line tripped
    the guard.

    The body is restricted to what a PEM block can actually contain. Left unanchored it ran
    from any BEGIN marker to the next END marker anywhere in the file, so a module that merely
    *mentions* both markers had the code between them deleted — and if that code was a whole
    function the result still parsed, so nothing noticed.
    """
    return re.sub(
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----[A-Za-z0-9+/=\s]*?-----END [A-Z ]*PRIVATE KEY-----",
        "***PRIVATE_KEY***",
        text,
    )


#: A secret-looking assignment whose value is either a quoted literal or a bare token.
#:
#: The bare alternative refuses anything that continues into an expression, because `token =
#: os.environ["K"]` names no secret and rewriting it only breaks the file. A bare value behind
#: a colon is held to a higher bar (see :func:`_looks_like_a_secret`) because `token: str` is
#: an ordinary annotation, not a leak.
_SECRET_ASSIGN = re.compile(
    r"""(?ix)
    (?<![A-Za-z0-9])                      # a whole word, not a suffix. Without this, `Token`
                                          # matched inside `CancellationToken` and broke 12 of
                                          # the 20 real .js files the rule touched. Underscore
                                          # is allowed, so `ZOTERO_API_KEY` still matches.
    (api[_-]?key|token|secret|password|passwd|pwd)   # 1: the name that makes this a secret
    (["']?[ \t]*(?P<sep>[:=])[ \t]*)      # 2: the assignment, preserved verbatim. Horizontal
                                          #    space only: `\s*` let a separator ending one
                                          #    line bind to the docstring opening the next.
                                          #    The optional quote closes a quoted *key*.
    (?:
        # A quoted literal. No whitespace inside, because a value that spans one would be
        # code: in `"…?token=" + tok + "&u="` the quote after `token=` CLOSES a literal, and
        # treating it as an opening one swallowed `+ tok +`. Non-empty, so the first two
        # quotes of a `\"\"\"` opener are never mistaken for an empty value.
        # Six characters, the same floor both scanner rules use. Without it the redactor and
        # the gate disagreed about `"pwd": "allow"` in opposite directions — the scanner
        # deliberately declines to call a five-letter permission a secret, and the redactor
        # masked it anyway.
        (?P<q>["'])(?P<quoted>[^"'\s]{6,})(?P=q)
      | (?P<bare>[A-Za-z0-9_\-]+)(?![\w\-.\[(])    # or a bare token that ends right here
    )
    """
)


def _looks_like_a_secret(value: str, *, aggressive: bool) -> bool:
    """Whether a bare, unquoted value is worth rewriting.

    An unquoted right-hand side is often code rather than a credential — ``token = None``,
    ``password = password or b""``, ``token: SecretStr`` — and rewriting one breaks the file.
    Requiring a digit, or real length, keeps ``hunter2`` and ``a1b2c3d4`` while leaving
    identifiers and type names alone.

    ``aggressive`` drops that bar, and is only justified where a parse check can veto the
    result. It must stay off otherwise: a fragment that never parsed, a language with no
    parser, and text inside a docstring all sit outside the guard's reach, and there a wider
    pattern is pure damage with nothing to catch it.
    """
    if aggressive:
        return len(value) >= 3
    if value.isdigit():
        return False  # `token=1048576` is a constant, not a credential
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value) and not any(c.isdigit() for c in value):
        # A bare identifier: `exports.createScalarToken = createScalarToken` names a symbol,
        # and rewriting it deletes a reference. Real credentials are not valid identifiers,
        # or at least carry a digit.
        return False
    return (any(c.isdigit() for c in value) and len(value) >= 6) or len(value) >= 16


def _redact_assignment(m: re.Match[str], *, aggressive: bool) -> str:
    quote = m.group("q")
    if quote is not None:
        return f"{m.group(1)}{m.group(2)}{quote}***{quote}"
    if not _looks_like_a_secret(m.group("bare"), aggressive=aggressive):
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

    Known bounds, all measured against ``benchmarks/ai_coding/cases`` and all currently at zero
    live occurrences, all covered by ``secrets_scan``:

    * In parse-verified Python no *bare* value is ever rewritten, because ``***`` is not a valid
      expression, so the veto discards every such rewrite. The aggressive path is therefore
      quoted-values-only for Python — which is not obvious from reading the pattern.
    * A camelCase name (``accessToken``) is not matched: the word boundary that stopped ``Token``
      matching inside ``CancellationToken`` excludes these too.
    * A language with no parser here gets the conservative rule and no veto, so whitespace-free
      or minified source can still be rewritten wrongly.
    * An *encrypted* PEM block is not matched, because its ``Proc-Type``/``DEK-Info`` headers
      fall outside the base64 body the pattern now requires.
    """
    from aibench.languages import registered_spec, spec_for_path

    spec = spec_for_path(path) if path else None
    if spec is None:
        spec = registered_spec(language)
    # The wide pattern is earned only when this exact input can be re-parsed afterwards. A
    # language with no parser here, or a chat-extracted fragment that never parsed to begin
    # with — 78% of real draft .py content — gets the conservative one, because there would be
    # nothing to catch an over-reach.
    verifiable = spec is not None and spec.parses(text) is True
    line_redacted = _redact_line_local(text, aggressive=verifiable)

    if verifiable and spec.parses(line_redacted) is False:
        # The whole rewrite broke it. Rather than discard every redaction over one bad line —
        # which would leave the other secrets in place — keep the ones that survive alone.
        lines = text.splitlines(keepends=True)
        kept = list(lines)
        for i, line in enumerate(lines):
            rewritten = _redact_line_local(line, aggressive=True)
            if rewritten == line:
                continue
            trial = [*kept[:i], rewritten, *kept[i + 1 :]]
            if spec.parses("".join(trial)) is not False:
                kept = trial
        line_redacted = "".join(kept)

    # Applied after the line work because it cannot be matched line by line — but under the
    # same veto, since it is not exempt from breaking a file. A declined PEM rewrite leaves the
    # key for `secrets_scan`'s own `private_key` rule to block, which is the trade this whole
    # function makes everywhere else.
    with_pem = _redact_spanning(line_redacted)
    if verifiable and spec.parses(with_pem) is False:
        return line_redacted
    return with_pem


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
                    "content": redact_source(str(art["content"]), path=str(art["path"])),
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
                    "content": redact_source(str(art["content"]), path=str(art["path"])),
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
