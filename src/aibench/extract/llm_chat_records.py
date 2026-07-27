"""Fetch and convert rows from MySQL table `llm_chat_records`."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterator
from urllib.parse import quote_plus

from aibench.extract.history_parse import (
    extract_user_agent,
    files_from_messages,
    gold_from_assistant,
    guess_language,
    guess_task_type,
    is_coding_record,
    normalize_messages,
    primary_user_prompt,
    tool_names,
)
from aibench.extract.sessions import (
    Message,
    SessionRecord,
    redact_secrets,
    session_to_case_draft,
    task_fingerprint,
)


@dataclass
class ChatRecord:
    request_id: str
    start_time: datetime | None
    model: str | None
    requests_tags: Any
    tools: Any
    full_history: Any
    key_alias: str | None
    created_at: datetime | None = None


def resolve_db_url(explicit: str | None = None) -> str:
    """Resolve SQLAlchemy URL from arg or env. Never hardcode secrets."""
    url = explicit or os.environ.get("AIBENCH_DB_URL") or os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError(
            "Database URL not set. Export AIBENCH_DB_URL, e.g.\n"
            "  export AIBENCH_DB_URL='mysql+pymysql://USER:PASS@HOST:3306/DB?charset=utf8mb4'"
        )
    return url


def build_mysql_url(
    *,
    user: str,
    password: str,
    host: str,
    port: int = 3306,
    database: str,
    charset: str = "utf8mb4",
) -> str:
    return (
        f"mysql+pymysql://{quote_plus(user)}:{quote_plus(password)}"
        f"@{host}:{port}/{database}?charset={charset}"
    )


def fetch_chat_records(
    db_url: str,
    *,
    limit: int = 500,
    min_messages: int = 3,
    max_messages: int = 80,
    only_opencode: bool = False,
    since: str | None = None,
    until: str | None = None,
    offset: int = 0,
) -> list[ChatRecord]:
    from sqlalchemy import create_engine, text

    engine = create_engine(
        db_url,
        pool_pre_ping=True,
        connect_args={"connect_timeout": 15, "read_timeout": 180, "write_timeout": 60},
    )
    clauses = ["full_history IS NOT NULL"]
    params: dict[str, Any] = {"limit": limit, "offset": offset, "min_n": min_messages, "max_n": max_messages}
    clauses.append("JSON_LENGTH(full_history) BETWEEN :min_n AND :max_n")
    if since:
        clauses.append("start_time >= :since")
        params["since"] = since
    if until:
        clauses.append("start_time < :until")
        params["until"] = until
    if only_opencode:
        clauses.append("CAST(requests_tags AS CHAR) LIKE :ua")
        params["ua"] = "%opencode%"

    sql = f"""
        SELECT request_id, start_time, model, requests_tags, tools, full_history, key_alias, created_at
        FROM llm_chat_records
        WHERE {' AND '.join(clauses)}
        ORDER BY start_time DESC
        LIMIT :limit OFFSET :offset
    """
    from aibench.retry import retry_call

    def _fetch() -> list[ChatRecord]:
        rows_out: list[ChatRecord] = []
        with engine.connect() as conn:
            rows = conn.execute(text(sql), params).mappings().all()
            for r in rows:
                rows_out.append(
                    ChatRecord(
                        request_id=str(r["request_id"]),
                        start_time=r.get("start_time"),
                        model=r.get("model"),
                        requests_tags=r.get("requests_tags"),
                        tools=r.get("tools"),
                        full_history=r.get("full_history"),
                        key_alias=r.get("key_alias"),
                        created_at=r.get("created_at"),
                    )
                )
        return rows_out

    return retry_call(_fetch, label="db_fetch_llm_chat_records")


def chat_record_to_session(rec: ChatRecord) -> SessionRecord | None:
    messages = normalize_messages(rec.full_history)
    if not messages:
        return None
    ua = extract_user_agent(rec.requests_tags)
    tools = tool_names(rec.tools)
    user_text = "\n".join(
        m["content"] for m in messages if m.get("role") in {"user", "human"}
    )
    if not is_coding_record(user_agent=ua, tools=tools, user_text=user_text):
        return None

    prompt = primary_user_prompt(messages)
    if not prompt or len(prompt) < 8:
        return None

    files = files_from_messages(messages)
    language = guess_language(prompt, files, [])
    gold = gold_from_assistant(messages, language)

    artifacts: list[dict[str, Any]] = []
    for f in files:
        artifacts.append(
            {
                "type": "context_file",
                "path": f["path"],
                "content": redact_secrets(f["content"]),
            }
        )
    for g in gold:
        artifacts.append(
            {
                "type": "accepted_file",
                "path": g["path"],
                "content": redact_secrets(g["content"]),
                "accepted": True,
            }
        )

    # Keep a compact message list for draft (user + short assistant tails)
    compact_msgs: list[Message] = []
    for m in messages:
        role = m.get("role") or "unknown"
        if role == "system":
            continue
        content = redact_secrets((m.get("content") or "")[:4000])
        if role == "tool":
            # keep truncated tool evidence only when files missing
            if files:
                continue
            content = content[:800]
        compact_msgs.append(
            Message(
                role=role if role != "tool" else "tool",
                content=content,
                metadata={"name": m.get("name")},
            )
        )

    # Ensure at least the primary prompt is present as a user message
    if not any(m.role in {"user", "human"} for m in compact_msgs):
        compact_msgs.insert(0, Message(role="user", content=redact_secrets(prompt)))

    product = "opencode" if ua and "opencode" in ua.lower() else (ua or "unknown")
    return SessionRecord(
        session_id=rec.request_id,
        messages=compact_msgs,
        created_at=rec.start_time.isoformat(sep=" ") if rec.start_time else None,
        language_hints=[language],
        artifacts=artifacts,
        outcome={
            "source_model": rec.model,
            "key_alias": rec.key_alias,
            "user_agent": ua,
            "tool_count": len(tools),
            "message_count": len(messages),
            "has_context_files": bool(files),
            "has_gold_code": bool(gold),
        },
        product=product,
    )


def record_to_case_draft(rec: ChatRecord) -> dict[str, Any] | None:
    """Direct high-signal conversion from a chat record to a case draft."""
    messages = normalize_messages(rec.full_history)
    if not messages:
        return None
    ua = extract_user_agent(rec.requests_tags)
    tools = tool_names(rec.tools)
    user_text = "\n".join(
        m["content"] for m in messages if m.get("role") in {"user", "human"}
    )
    if not is_coding_record(user_agent=ua, tools=tools, user_text=user_text):
        return None

    prompt = primary_user_prompt(messages)
    if not prompt or len(prompt) < 8:
        return None
    prompt = redact_secrets(prompt)

    files = files_from_messages(messages)
    language = guess_language(prompt, files, [])
    gold = gold_from_assistant(messages, language)
    task_type = guess_task_type(prompt)

    context_files = [
        {"path": f["path"], "content": redact_secrets(f["content"])} for f in files
    ]
    if not context_files:
        # Still allow pure "write from scratch" tasks
        context_files = [
            {
                "path": {
                    "python": "main.py",
                    "typescript": "main.ts",
                    "javascript": "main.js",
                    "java": "Main.java",
                    "cangjie": "main.cj",
                }.get(language, "main.txt"),
                "content": "# TODO: incomplete workspace snapshot from session\n",
            }
        ]

    gold_files = [
        {"path": g["path"], "content": redact_secrets(g["content"])} for g in gold
    ]

    if gold_files:
        grader: dict[str, Any] = {
            "mode": "gold",
            "match": "contains_key_lines",
            "key_lines": _key_lines_from_gold(gold_files[0]["content"]),
            "gold_files": gold_files,
        }
    else:
        grader = {
            "mode": "script",
            "command": "python -m pytest -q" if language == "python" else "true",
        }

    short_id = rec.request_id.replace("chatcmpl-", "")[:12]
    fp = task_fingerprint(prompt, [f["path"] for f in context_files])
    case_id = f"db-{short_id}-{fp}"

    return {
        "case_id": case_id,
        "schema_version": "0.1",
        "task_type": task_type,
        "language": language,
        "prompt": prompt,
        "context": {
            "files": context_files,
            "notes": (
                f"draft from llm_chat_records.request_id={rec.request_id}; "
                f"model={rec.model}; product={ua}; review+desensitize before publish"
            ),
        },
        "grader": grader,
        "metadata": {
            "source": "llm_chat_records",
            "source_session_id": rec.request_id,
            "difficulty": "medium",
            "tags": [
                "auto_draft",
                f"model:{(rec.model or 'unknown')}",
                f"product:{(ua or 'unknown')[:40]}",
            ],
            "split": "dev",
            "fingerprint": fp,
            "source_model": rec.model,
            "key_alias": rec.key_alias,
            "start_time": rec.start_time.isoformat(sep=" ") if rec.start_time else None,
            "message_count": len(messages),
            "tool_names": tools[:30],
            "has_context_files": bool(files),
            "has_gold_code": bool(gold),
            "review_status": "needs_review",
        },
    }


def _key_lines_from_gold(content: str, max_lines: int = 5) -> list[str]:
    lines = []
    for ln in content.splitlines():
        s = ln.strip()
        if not s or s.startswith("#") or s.startswith("//"):
            continue
        if any(k in s for k in ("def ", "class ", "function ", "import ", "return ", "const ", "let ")):
            lines.append(s[:120])
        if len(lines) >= max_lines:
            break
    if not lines:
        # fallback: first non-empty lines
        for ln in content.splitlines():
            s = ln.strip()
            if s:
                lines.append(s[:120])
            if len(lines) >= 3:
                break
    return lines


def extract_case_drafts_from_db(
    db_url: str,
    *,
    limit: int = 300,
    max_cases: int = 50,
    min_messages: int = 3,
    max_messages: int = 60,
    only_opencode: bool = True,
    require_gold: bool = False,
    since: str | None = None,
    until: str | None = None,
) -> list[dict[str, Any]]:
    records = fetch_chat_records(
        db_url,
        limit=limit,
        min_messages=min_messages,
        max_messages=max_messages,
        only_opencode=only_opencode,
        since=since,
        until=until,
    )
    drafts: list[dict[str, Any]] = []
    seen: set[str] = set()
    for rec in records:
        draft = record_to_case_draft(rec)
        if not draft:
            continue
        if require_gold and not draft["metadata"].get("has_gold_code"):
            continue
        fp = draft["metadata"]["fingerprint"]
        if fp in seen:
            continue
        seen.add(fp)
        drafts.append(draft)
        if len(drafts) >= max_cases:
            break
    return drafts
