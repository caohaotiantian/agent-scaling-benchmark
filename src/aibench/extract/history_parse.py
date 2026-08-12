"""Parse LiteLLM / OpenAI-style full_history into usable coding signals."""

from __future__ import annotations

import json
import re
from typing import Any

_PATH_CONTENT = re.compile(
    r"<path>(?P<path>[^<]+)</path>\s*<type>file</type>\s*<content>(?P<body>[\s\S]*?)</content>",
    re.I,
)
_CODE_FENCE = re.compile(r"```(?P<lang>[\w.+-]*)\n(?P<code>[\s\S]*?)```")
_FILE_HEADER = re.compile(
    r"(?:^|\n)(?:#\s*)?(?:File|文件|path)[:：]\s*(?P<path>[\w./\\-]+\.\w+)",
    re.I,
)

#: How a file's content reached us. A file the trace read in full is material a case can be
#: built on; a window of one is not, and the two arrive looking identical.
READ_COMPLETE = "read_complete"
READ_PARTIAL = "read_partial"

#: The read tool appends a line describing its own output inside `<content>`. Enumerated over
#: every line of the 23,868 files reconstructed for `_rev_raw4`: these five shapes are the only
#: ones present, 98.0% of files carry one, and not one ever occurs anywhere but the last line —
#: hence the line anchor. It matters: `filesystem.py` in that same corpus is a read-tool
#: implementation whose source builds these strings, and a substring match would eat real code.
_FOOTER_COMPLETE = re.compile(r"^\(End of file - total (?P<n>\d+) lines\)$")
_FOOTER_PARTIAL = re.compile(
    r"^\((?:Showing lines [\d-]+ of \d+\..*"
    r"|File has more lines\..*"
    r"|Output capped at .*"
    r"|Output truncated at .*)\)$"
)

CODING_TOOLS = {
    "bash",
    "read",
    "write",
    "edit",
    "glob",
    "grep",
    "ast_grep_search",
    "ast_grep_replace",
    "interactive_bash",
    "lsp_diagnostics",
    "file_write",
    "file_fetch",
    "exec",
}


def parse_jsonish(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, dict | list):
        return value
    if isinstance(value, bytes | bytearray):
        value = value.decode("utf-8", errors="replace")
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        try:
            return json.loads(s)
        except json.JSONDecodeError:
            return value
    return value


def content_to_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for p in content:
            if isinstance(p, dict):
                if p.get("type") == "text" or "text" in p:
                    parts.append(str(p.get("text") or ""))
                elif "content" in p:
                    parts.append(str(p.get("content") or ""))
                else:
                    parts.append(json.dumps(p, ensure_ascii=False))
            else:
                parts.append(str(p))
        return "\n".join(parts)
    if isinstance(content, dict):
        if "text" in content:
            return str(content.get("text") or "")
        return json.dumps(content, ensure_ascii=False)
    return str(content)


def normalize_messages(history: Any) -> list[dict[str, Any]]:
    history = parse_jsonish(history)
    if not isinstance(history, list):
        return []
    out: list[dict[str, Any]] = []
    for m in history:
        if not isinstance(m, dict):
            continue
        role = str(m.get("role") or "unknown")
        text = content_to_text(m.get("content"))
        out.append(
            {
                "role": role,
                "content": text,
                "tool_calls": m.get("tool_calls"),
                "name": m.get("name"),
                "raw": m,
            }
        )
    return out


def extract_user_agent(tags: Any) -> str | None:
    tags = parse_jsonish(tags)
    blob = ""
    if isinstance(tags, list):
        blob = " | ".join(str(x) for x in tags)
    elif isinstance(tags, str):
        blob = tags
    else:
        blob = str(tags or "")
    m = re.search(r"User-Agent:\s*([^|,\]]+)", blob, re.I)
    if m:
        return m.group(1).strip()
    if "opencode" in blob.lower():
        return "opencode"
    return None


def tool_names(tools: Any) -> list[str]:
    tools = parse_jsonish(tools)
    if not isinstance(tools, list):
        return []
    names: list[str] = []
    for t in tools:
        if not isinstance(t, dict):
            continue
        fn = t.get("function") if isinstance(t.get("function"), dict) else None
        name = None
        if fn:
            name = fn.get("name")
        name = name or t.get("name")
        if name:
            names.append(str(name))
    return names


def split_read_footer(content: str) -> tuple[str, str]:
    """Content without the read tool's trailing self-description, and how the file was read.

    The declared line count is checked rather than trusted. A read issued with an ``offset`` can
    reach the end of the file, and it is then footed "End of file" while carrying only the tail:
    1,076 of the corpus's 17,010 such footers (6.3%, and 8.0% of the Python ones) declare more
    lines than they hold. Reading the wording alone lets exactly the fragment this predicate
    exists to reject back in through the other branch.

    A tolerance of one line covers the blank line the tool leaves before the footer, which is
    what 15,834 of those 17,010 exhibit.
    """
    lines = content.splitlines()
    last = next((i for i in range(len(lines) - 1, -1, -1) if lines[i].strip()), None)
    if last is None:
        return content, READ_COMPLETE
    stripped = lines[last].strip()
    if _FOOTER_PARTIAL.match(stripped):
        return "\n".join(lines[:last]).rstrip() + "\n", READ_PARTIAL
    m = _FOOTER_COMPLETE.match(stripped)
    if m is None:
        return content, READ_COMPLETE
    body = "\n".join(lines[:last]).rstrip() + "\n"
    declared = int(m.group("n"))
    actual = len(body.splitlines())
    return body, (READ_COMPLETE if abs(declared - actual) <= 1 else READ_PARTIAL)


def extract_files_from_tool_text(text: str) -> list[dict[str, str]]:
    files: list[dict[str, str]] = []
    for m in _PATH_CONTENT.finditer(text or ""):
        path = m.group("path").strip().replace("\\", "/")
        # keep basename-ish relative path when absolute
        if ":" in path and len(path) > 2 and path[1] == ":":
            # Windows absolute
            path = path.split("/")[-1] if "/" in path else path.split("\\")[-1]
        elif path.startswith("/"):
            path = "/".join(path.split("/")[-3:])
        body = m.group("body")
        # strip line-number prefixes like "12: code"
        cleaned_lines = []
        for line in body.splitlines():
            if re.match(r"^\s*\d+:", line):
                cleaned_lines.append(re.sub(r"^\s*\d+:\s?", "", line))
            else:
                cleaned_lines.append(line)
        content = "\n".join(cleaned_lines).strip() + "\n"
        content, origin = split_read_footer(content)
        if path and content.strip():
            files.append({"path": path, "content": content[:200_000], "origin": origin})
    return files


def extract_code_blocks(text: str) -> list[dict[str, str]]:
    blocks: list[dict[str, str]] = []
    for m in _CODE_FENCE.finditer(text or ""):
        lang = (m.group("lang") or "").strip().lower()
        code = m.group("code")
        if len(code.strip()) < 20:
            continue
        blocks.append({"lang": lang, "content": code})
    return blocks


def guess_language(prompt: str, files: list[dict[str, str]], blocks: list[dict[str, str]]) -> str:
    text = prompt.lower()
    if "python" in text or "pytest" in text or ".py" in text:
        return "python"
    if "typescript" in text or ".ts" in text:
        return "typescript"
    if "javascript" in text or ".js" in text:
        return "javascript"
    if "java" in text and "javascript" not in text:
        return "java"
    if "cangjie" in text or ".cj" in text:
        return "cangjie"
    for f in files:
        if f["path"].endswith(".py"):
            return "python"
        if f["path"].endswith(".ts"):
            return "typescript"
        if f["path"].endswith(".js"):
            return "javascript"
        if f["path"].endswith(".cj"):
            return "cangjie"
    for b in blocks:
        if b.get("lang") in {"python", "py"}:
            return "python"
        if b.get("lang") in {"ts", "typescript"}:
            return "typescript"
        if b.get("lang") in {"js", "javascript"}:
            return "javascript"
    return "python"


def guess_task_type(prompt: str) -> str:
    p = prompt.lower()
    if any(k in p for k in ("修复", "bug", "fix", "报错", "error", "崩溃")):
        return "bugfix"
    if any(k in p for k in ("重构", "refactor", "清理")):
        return "refactor"
    if any(k in p for k in ("测试", "test", "pytest", "单元测试")):
        return "test_gen"
    if any(k in p for k in ("解释", "总结", "说明", "设计方案")):
        return "explain_to_edit"
    return "feature"


def is_coding_record(
    *,
    user_agent: str | None,
    tools: list[str],
    user_text: str,
) -> bool:
    ua = (user_agent or "").lower()
    if "opencode" in ua or "codegenie" in ua:
        return True
    if set(tools) & CODING_TOOLS and re.search(
        r"(代码|实现|修复|重构|函数|bug|fix|implement|refactor|class |def |import |编译|测试|pytest|error)",
        user_text,
        re.I,
    ):
        return True
    return bool(
        re.search(
            r"(实现|修复|重构|bug|fix|implement|refactor|写一个|编写一个|python|代码|函数|算法)",
            user_text,
            re.I,
        )
    )


def primary_user_prompt(messages: list[dict[str, Any]]) -> str:
    users = [m for m in messages if m.get("role") in {"user", "human"}]
    if not users:
        return ""
    # Prefer first non-meta user message; fall back to last if multi-turn refinement.
    cleaned: list[str] = []
    for m in users:
        text = (m.get("content") or "").strip()
        if not text:
            continue
        # strip common plan-mode / system-reminder noise
        text = re.sub(r"<system-reminder>[\s\S]*?</system-reminder>", "", text, flags=re.I)
        text = re.sub(r"text:", "", text, count=1).strip() if text.startswith("text:") else text
        if len(text) < 4:
            continue
        # skip pure tool-injected metadata blobs
        if text.startswith("Sender (untrusted metadata)"):
            continue
        cleaned.append(text)
    if not cleaned:
        return ""
    # If conversation refined the task, last user is often the real ask.
    if len(cleaned) == 1:
        return cleaned[0][:8000]
    # Combine first intent + last refinement when short enough
    first, last = cleaned[0], cleaned[-1]
    if first == last:
        return first[:8000]
    if len(last) >= 20:
        return last[:8000]
    return (first + "\n\n" + last)[:8000]


def files_from_messages(messages: list[dict[str, Any]]) -> list[dict[str, str]]:
    by_path: dict[str, str] = {}
    for m in messages:
        if m.get("role") != "tool":
            continue
        for f in extract_files_from_tool_text(m.get("content") or ""):
            by_path[f["path"]] = f["content"]
    return [{"path": k, "content": v} for k, v in by_path.items()]


def gold_from_assistant(messages: list[dict[str, Any]], language: str) -> list[dict[str, str]]:
    """Best-effort gold: last substantial code block from assistant turns."""
    blocks: list[dict[str, str]] = []
    for m in messages:
        if m.get("role") != "assistant":
            continue
        blocks.extend(extract_code_blocks(m.get("content") or ""))
    if not blocks:
        return []
    # Prefer language-matching last block
    lang_aliases = {
        "python": {"python", "py", ""},
        "typescript": {"typescript", "ts"},
        "javascript": {"javascript", "js"},
        "java": {"java"},
        "cangjie": {"cangjie", "cj"},
    }
    aliases = lang_aliases.get(language, {language, ""})
    preferred = [b for b in blocks if b.get("lang") in aliases]
    chosen = preferred[-1] if preferred else blocks[-1]
    ext = {
        "python": "solution.py",
        "typescript": "solution.ts",
        "javascript": "solution.js",
        "java": "Solution.java",
        "cangjie": "solution.cj",
    }.get(language, "solution.txt")
    return [{"path": ext, "content": chosen["content"].rstrip() + "\n"}]
