"""Parse LiteLLM / OpenAI-style full_history into usable coding signals."""

from __future__ import annotations

import itertools
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
#: No footer at all, so the tool never said which of the two this is. 470 of the 23,868 files in
#: the current pool (2.0%, and 416 of the 6,021 Python ones) are like this. Calling them complete
#: would vouch for them on no evidence, which is the whole thing this distinction exists to stop.
READ_UNKNOWN = "read_unknown"

#: The read tool appends a line describing its own output inside `<content>`. Enumerated over
#: every line of the 23,868 files reconstructed for `_rev_raw4`: these five shapes are the only
#: ones present and 98.0% of files carry one.
#:
#: What keeps a line of real source safe is `split_read_footer` inspecting only the last
#: non-blank line — `filesystem.py` in that same corpus is a read-tool implementation that
#: builds one of these strings on its line 199 of 833, and it is out of reach for that reason,
#: not because of the anchors. The anchors are the narrower second guard: they are what stops a
#: last line that merely *contains* a footer, such as `log("(End of file - total 3 lines)")`,
#: from being mistaken for one.
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


#: How much of an absolute path survives extraction. Everything above this is discarded, so no
#: later comparison may treat a difference above it as evidence about file identity.
PATH_TAIL_COMPONENTS = 3


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

    The comparison is exact. The tool leaves one blank line between the body and the footer, and
    only that one is discounted — collapsing every trailing blank line instead made the check
    measure "how many blank lines does this file end with" rather than "is anything missing",
    and misfiled 61 whole files as fragments because they genuinely end in blank lines.
    """
    lines = content.splitlines()
    last = next((i for i in range(len(lines) - 1, -1, -1) if lines[i].strip()), None)
    if last is None:
        return content, READ_UNKNOWN
    stripped = lines[last].strip()
    body_lines = lines[:last]
    if body_lines and not body_lines[-1].strip():
        body_lines = body_lines[:-1]  # the separator the tool inserts, not part of the file
    body = ("\n".join(body_lines) + "\n") if body_lines else ""
    if _FOOTER_PARTIAL.match(stripped):
        return body, READ_PARTIAL
    m = _FOOTER_COMPLETE.match(stripped)
    if m is None:
        return content, READ_UNKNOWN
    return body, (READ_COMPLETE if int(m.group("n")) == len(body_lines) else READ_PARTIAL)


_NUMBERED_LINE = re.compile(r"^\s*(?P<n>\d+):\s?")


def _strip_line_numbers(lines: list[str]) -> list[str]:
    """Remove a read tool's ``12: `` gutter, and only that.

    The strip used to run per line, unconditionally, so any *source* line beginning with digits
    and a colon lost its leading token. That is not a rare shape — ``404: "not found",`` in a
    dict, ``8080: {`` in a config, a YAML mapping keyed by number — and the damage is silent:
    the reconstructed file is subtly wrong, and it becomes the stub or the reference solution of
    a case.

    A gutter is recognisable by being a *gutter*: every non-blank line carries one and the
    numbers run consecutively. One line matching proves nothing, which is exactly the case the
    old rule got wrong.
    """
    numbers: list[int] = []
    for line in lines:
        if not line.strip():
            continue
        match = _NUMBERED_LINE.match(line)
        if not match:
            return lines
        numbers.append(int(match.group("n")))
    if len(numbers) < 2:
        # A one-line block gives no evidence either way, and mangling it costs more than
        # leaving a stray gutter on it.
        return lines
    if numbers[0] < 1:
        # Every read tool here numbers from 1 — its own footers say "Showing lines 1-40 of 190".
        # A block starting at 0 is source: `{0: "Sun", 1: "Mon", 2: "Tue"}` is otherwise
        # indistinguishable from a gutter.
        return lines
    if any(b - a != 1 for a, b in itertools.pairwise(numbers)):
        return lines
    return [_NUMBERED_LINE.sub("", line) if line.strip() else line for line in lines]


def extract_files_from_tool_text(text: str) -> list[dict[str, str]]:
    files: list[dict[str, str]] = []
    for m in _PATH_CONTENT.finditer(text or ""):
        original = m.group("path").strip()
        path = original.replace("\\", "/")
        # keep basename-ish relative path when absolute
        if ":" in path and len(path) > 2 and path[1] == ":":
            # Windows absolute
            path = path.split("/")[-1] if "/" in path else path.split("\\")[-1]
        elif path.startswith("/"):
            path = "/".join(path.split("/")[-PATH_TAIL_COMPONENTS:])
        # The footer is split off before any trimming. Trimming first ate the blank lines a file
        # genuinely ends with, and the line count then came up short — a whole file refused as a
        # window for ending in whitespace. Only newlines are trimmed, never indentation: the old
        # `.strip()` dedented the first line of every file it touched.
        #
        # It is also split off *before* the gutter test, and that ordering is load-bearing. The
        # gutter rule asks whether every non-blank line is numbered; the footer is a line and it
        # is not numbered, so testing the un-split body answered "no" for the 98.0% of files
        # that carry one — the gutter then survived into `pre` and `post`, became a stub and a
        # reference solution, and was stamped `read_complete`.
        content, origin = split_read_footer(m.group("body").strip("\n"))
        content = "\n".join(_strip_line_numbers(content.splitlines())).strip("\n")
        if path and content.strip():
            files.append(
                {
                    "path": path,
                    # The path before truncation. A Windows path is reduced to its basename
                    # here, so two genuinely different files under one name collapse to a
                    # single entry and the replay's ambiguity check has nothing left to
                    # compare. Keeping the original is what lets it see the collision.
                    "source_path": original,
                    "content": content[:200_000] + "\n",
                    "origin": origin,
                }
            )
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
