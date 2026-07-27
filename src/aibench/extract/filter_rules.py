"""Rule-based draft/session quality filter for coding benchmark suitability."""

from __future__ import annotations

import re
from dataclasses import dataclass, asdict
from typing import Any


_DROP_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("ops_disk_health", re.compile(r"(巡检|磁盘健康|硬盘运行|disk health|smartctl)", re.I)),
    ("heartbeat", re.compile(r"HEARTBEAT_OK|HEARTBEAT\.md", re.I)),
    ("judge_meta", re.compile(r"(任务完成度评测|rubric-judge|<<RUBRIC>>|is_correct)", re.I)),
    ("pure_chat", re.compile(r"^(今天天气|你好|在吗)[\s\S]{0,40}$", re.I)),
    ("system_only_noise", re.compile(r"WITTY_INTERNAL_INITIATOR|openclaw-tui.*HEARTBEAT", re.I)),
    ("explain_only", re.compile(
        r"(设计方案|总结一下|详细说明|是怎么定义的|默认值从何而来|虚拟大小是怎么|"
        r"Automatically detect the language|superpowers|EXTREMELY_IMPORTANT)",
        re.I,
    )),
    ("log_dump", re.compile(r"匹配\d+次|\.log\s*（匹配", re.I)),
]

_KEEP_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("coding_intent", re.compile(
        r"(实现|修复|重构|bug|fix|implement|refactor|编写|函数|接口|api|pytest|单元测试|"
        r"切换到|报错|compile|class |def |import )",
        re.I,
    )),
    ("code_fence", re.compile(r"```[\w]*\n")),
]


@dataclass
class FilterDecision:
    keep: bool
    reason: str
    score: float
    labels: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def rule_filter_text(
    prompt: str,
    *,
    has_context_files: bool = False,
    has_gold_code: bool = False,
    product: str | None = None,
    tool_names: list[str] | None = None,
) -> FilterDecision:
    text = (prompt or "").strip()
    labels: list[str] = []
    if len(text) < 8:
        return FilterDecision(False, "prompt_too_short", 0.0, ["too_short"])

    for name, pat in _DROP_PATTERNS:
        if pat.search(text):
            return FilterDecision(False, f"drop_rule:{name}", 0.0, [name])

    score = 0.0
    for name, pat in _KEEP_PATTERNS:
        if pat.search(text):
            score += 1.0
            labels.append(name)

    prod = (product or "").lower()
    if "opencode" in prod or "codegenie" in prod:
        score += 1.5
        labels.append("coding_agent")

    tools = set(tool_names or [])
    if tools & {"bash", "read", "write", "edit", "grep", "glob"}:
        score += 1.0
        labels.append("coding_tools")

    if has_context_files:
        score += 1.0
        labels.append("has_context")
    if has_gold_code:
        score += 0.5
        labels.append("has_gold")

    if score < 1.0:
        return FilterDecision(False, "low_coding_signal", score, labels or ["no_signal"])
    # Prefer cases with real file context for restoration credibility
    if not has_context_files and score < 2.5:
        return FilterDecision(False, "no_workspace_context", score, labels + ["no_context"])
    return FilterDecision(True, "ok", score, labels)


def rule_filter_draft(draft: dict[str, Any]) -> FilterDecision:
    meta = draft.get("metadata") or {}
    prompt = draft.get("prompt") or ""
    files = ((draft.get("context") or {}).get("files")) or []
    real_files = [
        f
        for f in files
        if not str(f.get("content") or "").startswith("# TODO: incomplete workspace")
    ]
    tools = meta.get("tool_names") or []
    product = None
    tags = meta.get("tags") or []
    for t in tags:
        if isinstance(t, str) and t.startswith("product:"):
            product = t.split(":", 1)[1]
    return rule_filter_text(
        prompt,
        has_context_files=bool(real_files) or bool(meta.get("has_context_files")),
        has_gold_code=bool(meta.get("has_gold_code")),
        product=product,
        tool_names=list(tools) if isinstance(tools, list) else [],
    )
