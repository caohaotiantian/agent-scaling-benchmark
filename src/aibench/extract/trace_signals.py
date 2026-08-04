"""Process signals mined from a production trace, and the tier they suggest.

``record_to_case_draft`` keeps only the prompt, the files and the final code — it throws away
*how* the original session got there. That process is the best difficulty evidence available:
a task where the engineer searched three times, edited four files and re-ran the tests twice
is structurally harder than one solved by a single edit, and no amount of reading the prompt
reveals that.

The suggestion is a starting point, not a verdict. ``tiers.check_tier_invariants`` still has to
confirm the generated case actually has the structure its tier claims; a case that cannot be
built at the suggested tier is downgraded rather than mislabelled.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from typing import Any

from aibench.extract.history_parse import parse_jsonish

READ_TOOLS = {"read", "view", "cat", "open", "file_read", "list", "ls", "glob", "readfile"}
SEARCH_TOOLS = {"grep", "search", "ripgrep", "rg", "find", "ast_grep_search", "codebase_search"}
EDIT_TOOLS = {
    "write",
    "edit",
    "multiedit",
    "file_write",
    "patch",
    "apply_patch",
    "str_replace",
    "str_replace_editor",
    "ast_grep_replace",
}
EXEC_TOOLS = {"bash", "exec", "shell", "run", "run_command", "interactive_bash", "terminal"}

_TEST_CMD = re.compile(
    r"\b(?:pytest|py\.test|unittest|nose2|tox|jest|vitest|mocha|"
    r"(?:npm|yarn|pnpm)\s+(?:run\s+)?test|go\s+test|cargo\s+test|mvn\s+test|gradle\s+test|"
    r"make\s+(?:test|check))\b",
    re.I,
)
_ERROR_SIGNAL = re.compile(
    r"Traceback \(most recent call last\)|AssertionError|\bFAILED\b|\bERROR\b|"
    r"SyntaxError|ImportError|ModuleNotFoundError|TypeError|\bexit(?:\s+code)?[=:\s]+[1-9]",
)
_PATH_KEYS = ("path", "file_path", "filePath", "filename", "file", "target_file", "notebook_path")
_CMD_KEYS = ("command", "cmd", "script", "input")
_TOOL_FILE_BLOCK = re.compile(r"<path>([^<]+)</path>", re.I)


@dataclass
class TraceSignals:
    """Counts of what the original session actually did."""

    user_turns: int = 0
    assistant_turns: int = 0
    tool_calls: int = 0
    distinct_tools: list[str] = field(default_factory=list)
    read_ops: int = 0
    search_ops: int = 0
    edit_ops: int = 0
    exec_ops: int = 0
    test_runs: int = 0
    files_read: int = 0
    files_touched: int = 0
    repair_rounds: int = 0
    error_signals: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _tool_kind(name: str) -> str | None:
    n = (name or "").strip().lower()
    if n in READ_TOOLS:
        return "read"
    if n in SEARCH_TOOLS:
        return "search"
    if n in EDIT_TOOLS:
        return "edit"
    if n in EXEC_TOOLS:
        return "exec"
    return None


def _call_name_and_args(call: Any) -> tuple[str, dict[str, Any]]:
    if not isinstance(call, dict):
        return "", {}
    fn = call.get("function") if isinstance(call.get("function"), dict) else None
    name = str((fn or {}).get("name") or call.get("name") or "")
    raw_args = (fn or {}).get("arguments", call.get("arguments"))
    args = parse_jsonish(raw_args)
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except json.JSONDecodeError:
            args = {}
    return name, args if isinstance(args, dict) else {}


def _first_str(args: dict[str, Any], keys: tuple[str, ...]) -> str:
    for k in keys:
        val = args.get(k)
        if isinstance(val, str) and val.strip():
            return val
    return ""


def signals_from_messages(messages: list[dict[str, Any]]) -> TraceSignals:
    """Mine a normalized message list (see ``history_parse.normalize_messages``)."""
    sig = TraceSignals()
    tools_seen: set[str] = set()
    read_paths: set[str] = set()
    touched_paths: set[str] = set()
    events: list[str] = []

    for msg in messages:
        role = str(msg.get("role") or "")
        content = str(msg.get("content") or "")
        if role in {"user", "human"}:
            sig.user_turns += 1
        elif role == "assistant":
            sig.assistant_turns += 1
        elif role == "tool":
            if _ERROR_SIGNAL.search(content):
                sig.error_signals += 1
            # opencode-style tool output embeds the file it read.
            read_paths.update(p.strip() for p in _TOOL_FILE_BLOCK.findall(content))

        for call in msg.get("tool_calls") or []:
            name, args = _call_name_and_args(call)
            if not name:
                continue
            sig.tool_calls += 1
            tools_seen.add(name.lower())
            kind = _tool_kind(name)
            if kind == "read":
                sig.read_ops += 1
                if path := _first_str(args, _PATH_KEYS):
                    read_paths.add(path)
            elif kind == "search":
                sig.search_ops += 1
            elif kind == "edit":
                sig.edit_ops += 1
                events.append("edit")
                if path := _first_str(args, _PATH_KEYS):
                    touched_paths.add(path)
            elif kind == "exec":
                sig.exec_ops += 1
                cmd = _first_str(args, _CMD_KEYS)
                if _TEST_CMD.search(cmd):
                    sig.test_runs += 1
                    events.append("test")

    sig.distinct_tools = sorted(tools_seen)
    sig.files_read = len(read_paths)
    sig.files_touched = len(touched_paths)
    sig.repair_rounds = _count_repair_rounds(events)
    return sig


def _count_repair_rounds(events: list[str]) -> int:
    """Number of test → edit → test cycles, i.e. how often feedback drove another fix."""
    rounds = 0
    tested = False
    edited_since_test = False
    for ev in events:
        if ev == "test":
            if tested and edited_since_test:
                rounds += 1
            tested = True
            edited_since_test = False
        elif ev == "edit" and tested:
            edited_since_test = True
    return rounds


def suggest_tier(sig: TraceSignals) -> tuple[str, list[str]]:
    """Map process signals to the highest tier the original session gives evidence for.

    Checked from the top down: the first rule that fires wins, so a session only reaches T4/T5
    when it genuinely spread work across files or iterated on test feedback.
    """
    reasons: list[str] = []
    if sig.repair_rounds >= 2 and sig.files_touched >= 3:
        reasons.append(f"repair_rounds={sig.repair_rounds}, files_touched={sig.files_touched}")
        return "T5", reasons
    if sig.files_touched >= 3:
        reasons.append(f"files_touched={sig.files_touched}")
        return "T4", reasons
    if sig.search_ops >= 3 and sig.files_read >= 5:
        reasons.append(f"search_ops={sig.search_ops}, files_read={sig.files_read}")
        return "T4", reasons
    if sig.test_runs >= 1 and sig.error_signals >= 1:
        reasons.append(f"test_runs={sig.test_runs}, error_signals={sig.error_signals}")
        return "T3", reasons
    if sig.repair_rounds >= 1:
        reasons.append(f"repair_rounds={sig.repair_rounds}")
        return "T3", reasons
    if sig.error_signals >= 1 or sig.files_read >= 2 or sig.search_ops >= 1:
        reasons.append(
            f"error_signals={sig.error_signals}, files_read={sig.files_read}, "
            f"search_ops={sig.search_ops}"
        )
        return "T2", reasons
    reasons.append("no retrieval, iteration or failure evidence in trace")
    return "T1", reasons
