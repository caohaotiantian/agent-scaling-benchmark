"""Discrimination tiers: what a case structurally forces the solver to do.

Difficulty is *not* estimated from size here. Sizing a case by LOC and file count
(``validity.estimate_difficulty``) put 93.8% of the first auto-generated set into a single
"medium" bucket while every one of those cases was solved in two agent steps — the metric
tracked file volume, not the work required.

A tier instead declares which capabilities a case cannot be solved without, and each tier
carries invariants that a machine can check offline:

    T1 直接修复    题面/注释直接给出缺陷位置                  地板锚，几乎所有组合都过
    T2 定位修复    只给现象，求解者必须自己定位                A1 诊断
    T3 隐藏规格    隐藏测试 + 参考解，可见测试只是冒烟          A5 规格遵从 / A6 抗过拟合
    T4 上下文检索  ≥5 文件 + 干扰文件，缺陷藏在其中            A2 检索
    T5 跨文件自修复 T4 + 缺陷跨 ≥2 文件 + 更宽的隐藏测试面      A3 迭代自修复 / A4 跨文件一致性

The disclosure detector below is deliberately conservative: it over-flags rather than
under-flags. A false positive costs one regenerated prompt at build time; a false negative
ships a case that hands the answer to every model and separates nothing.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

from aibench import languages
from aibench.models import Case

AXES: dict[str, str] = {
    "A1": "定位诊断",
    "A2": "上下文检索",
    "A3": "迭代自修复",
    "A4": "跨文件一致性",
    "A5": "规格遵从",
    "A6": "抗过拟合",
}

TIER_ORDER = ("T1", "T2", "T3", "T4", "T5")


@dataclass(frozen=True)
class TierSpec:
    """Structural contract for one tier. Every field is checkable without running the case."""

    tier: str
    label: str
    axes: tuple[str, ...]
    min_files: int
    max_files: int | None
    allow_disclosure: bool
    min_hidden_test_fns: int
    min_distractors: int
    min_solution_files: int
    require_protected_paths: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


TIER_SPECS: dict[str, TierSpec] = {
    "T1": TierSpec(
        tier="T1",
        label="直接修复",
        axes=(),
        min_files=1,
        max_files=3,
        allow_disclosure=True,
        min_hidden_test_fns=0,
        min_distractors=0,
        min_solution_files=0,
        require_protected_paths=False,
    ),
    "T2": TierSpec(
        tier="T2",
        label="定位修复",
        axes=("A1",),
        min_files=2,
        max_files=4,
        allow_disclosure=False,
        min_hidden_test_fns=0,
        min_distractors=0,
        min_solution_files=0,
        require_protected_paths=False,
    ),
    "T3": TierSpec(
        tier="T3",
        label="隐藏规格",
        axes=("A1", "A5", "A6"),
        min_files=2,
        max_files=6,
        allow_disclosure=False,
        min_hidden_test_fns=2,
        min_distractors=0,
        min_solution_files=1,
        require_protected_paths=True,
    ),
    "T4": TierSpec(
        tier="T4",
        label="上下文检索",
        axes=("A1", "A2", "A5", "A6"),
        min_files=5,
        max_files=None,
        allow_disclosure=False,
        min_hidden_test_fns=3,
        min_distractors=1,
        # One file, deliberately: T4 asks "can it find the broken file among many", and a
        # forced-T4 probe produced a two-file defect 0 times in 10. Cross-file consistency is
        # a different capability and lives at T5, so neither is unmeasurable because of the
        # other.
        min_solution_files=1,
        require_protected_paths=True,
    ),
    "T5": TierSpec(
        tier="T5",
        label="迭代自修复",
        axes=("A1", "A2", "A3", "A4", "A5", "A6"),
        min_files=5,
        max_files=None,
        allow_disclosure=False,
        min_hidden_test_fns=4,
        min_distractors=1,
        min_solution_files=2,
        require_protected_paths=True,
    ),
}

# Phrases that name the defect *mechanism* or its exact location, as opposed to describing the
# observable symptom. Calibrated against the 64 prompts in the first auto-generated set.
_DISCLOSURE_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("explicit_bug_pointer", re.compile(r"\bthe\s+bug\s+is\s+(?:in|at|on)\b", re.I)),
    ("line_number", re.compile(r"\b(?:on|at|in)\s+line\s+\d+\b|第\s*\d+\s*行", re.I)),
    ("cn_root_cause", re.compile(r"(?:问题|错误|缺陷|bug)\s*(?:出在|在于|位于)", re.I)),
    ("uses_instead_of", re.compile(r"\bus(?:es|ing|ed)\b[^.。;；]{0,80}\binstead of\b", re.I)),
    ("inverted", re.compile(r"\b(?:is|are|was|were)\s+inverted\b|\binverted\s+\w+", re.I)),
    ("swapped", re.compile(r"\b(?:is|are|was|were|gets?)\s+swapped\b|\bswapped\s+\w+", re.I)),
    ("off_by_one", re.compile(r"\boff[-\s]?by[-\s]?one\b", re.I)),
    (
        "wrong_mechanism",
        re.compile(
            r"\b(?:wrong|incorrect|bad)\s+"
            r"(?:diagonal|offset|index|indices|sign|operator|comparison|variable|key|field|"
            r"argument|parameter|delimiter|separator|version|order\s+of|direction|branch|"
            r"condition|boundary|slice|axis|dimension)\b",
            re.I,
        ),
    ),
    (
        "missing_construct",
        re.compile(
            r"\bmissing\s+(?:the\s+)?"
            r"(?:argument|parameter|call\s+to|return|break|await|import|guard|check\s+for)\b",
            re.I,
        ),
    ),
    ("replace_directive", re.compile(r"\b(?:replace|change)\s+`[^`]+`\s+(?:with|to)\b", re.I)),
    ("cn_replace_directive", re.compile(r"把\s*`?[^`\s，。]+`?\s*(?:改成|换成|替换为|改为)")),
    # Only statements about what the CURRENT code does. "应该返回 X" states the expected
    # behaviour, which every task description needs, so it is deliberately not matched here.
    (
        "cn_current_impl",
        re.compile(
            r"(?:当前|现在|目前|原来)(?:的)?(?:实现|代码|逻辑|版本)?[^。；\n]{0,20}"
            r"(?:用了|使用了|写成了|少了|漏了|多了|写反|搞反|颠倒)"
        ),
    ),
    ("cn_reversed", re.compile(r"(?:写反了|搞反了|顺序(?:弄|搞)?反了|正好相反)")),
    ("inline_bug_marker", re.compile(r"(?:^|\s)(?:#\s*)?(?:BUG|FIXME|XXX)\s*[:：]", re.I)),
    ("still_uses_old", re.compile(r"(?:仍(?:然)?使用|still\s+use[sd]?)\s*`?[\w/.-]+`?", re.I)),
]

# Comment markers that point a solver straight at the broken line.
_BUG_MARKER = re.compile(
    r"(?m)^\s*(?:#|//|/\*|\*)\s*(?:BUG|FIXME|XXX|HACK|WRONG|INCORRECT)\b", re.I
)
_TODO_MARKER = re.compile(r"(?m)^\s*(?:#|//|/\*|\*)\s*TODO\b", re.I)


@dataclass
class TierViolation:
    code: str
    message: str
    severity: str = "error"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TierCheck:
    tier: str
    ok: bool
    violations: list[TierViolation] = field(default_factory=list)
    facts: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "tier": self.tier,
            "ok": self.ok,
            "violations": [v.to_dict() for v in self.violations],
            "facts": self.facts,
        }


def tier_spec(tier: str) -> TierSpec:
    try:
        return TIER_SPECS[tier]
    except KeyError:
        raise ValueError(f"unknown tier {tier!r}; expected one of {list(TIER_ORDER)}") from None


def find_disclosures(prompt: str) -> list[str]:
    """Names of disclosure patterns present in the prompt (empty means symptom-only)."""
    return [name for name, pat in _DISCLOSURE_PATTERNS if pat.search(prompt or "")]


def merge_disclosure_findings(
    regex_hits: list[str],
    llm_disclosed: bool | None,
    llm_reason: str = "",
) -> list[TierViolation]:
    """Combine the deterministic detector with an optional LLM second opinion.

    The regex is authoritative and blocking; the reviewer can only add a warning. An LLM that
    is unavailable, unparseable, or simply wrong must never be able to fail a case on its own,
    and it must never override a pattern that did fire.
    """
    if regex_hits:
        return [
            TierViolation(
                "prompt_discloses_defect",
                f"prompt names the defect rather than the symptom: {regex_hits}",
            )
        ]
    if llm_disclosed:
        return [
            TierViolation(
                "prompt_discloses_defect_llm",
                f"reviewer flagged paraphrased disclosure the patterns missed: {llm_reason[:200]}",
                severity="warn",
            )
        ]
    return []


def find_bug_markers(content: str, *, flag_todo: bool = False) -> bool:
    if _BUG_MARKER.search(content or ""):
        return True
    return bool(flag_todo and _TODO_MARKER.search(content or ""))


def strip_bug_markers(content: str, *, strip_todo: bool = False) -> str:
    """Drop whole-line defect markers, and trailing ones, from source text."""
    out: list[str] = []
    for line in (content or "").splitlines():
        if _BUG_MARKER.match(line) or (strip_todo and _TODO_MARKER.match(line)):
            continue
        out.append(
            re.sub(
                r"\s*(?:#|//)\s*(?:BUG|FIXME|XXX|HACK|WRONG|INCORRECT)\b.*$", "", line, flags=re.I
            )
        )
    text = "\n".join(out)
    return text + "\n" if (content or "").endswith("\n") and not text.endswith("\n") else text


def count_test_functions(content: str, *, language: str | None = None) -> int:
    """Tests declared in a file, per the language registry."""
    return languages.count_test_functions(content, language=language)


#: Above this share of changed lines a reference solution reads as a rewrite rather than a fix.
REWRITE_RATIO = 0.6


def solution_change_ratio(original: str, solution: str) -> float:
    """Share of the reference solution's lines that differ from the starting file."""
    import difflib

    a = (original or "").splitlines()
    b = (solution or "").splitlines()
    if not b:
        return 0.0
    same = sum(
        block.size
        for block in difflib.SequenceMatcher(None, a, b, autojunk=False).get_matching_blocks()
    )
    return 1.0 - (same / len(b))


def _check_solution_minimality(case: Case) -> list[TierViolation]:
    """A reference solution should demonstrate a localized fix, not replace the file.

    A wholesale rewrite still passes the solvability gate, so nothing else would notice — yet
    it proves nothing about where the defect was, and at T4 it makes "the fix spans two files"
    unfalsifiable.
    """
    by_path = {fb.path: fb.content for fb in case.files}
    out: list[TierViolation] = []
    for gf in case.grader.gold_files:
        original = by_path.get(gf.path)
        if original is None:
            continue  # a new file the solution adds; nothing to compare against
        ratio = solution_change_ratio(original, gf.content)
        if ratio == 0.0:
            out.append(
                TierViolation(
                    "solution_file_unchanged",
                    f"reference solution for {gf.path} is identical to the starting file",
                )
            )
        elif ratio > REWRITE_RATIO:
            out.append(
                TierViolation(
                    "solution_rewrites_file",
                    f"reference solution changes {ratio:.0%} of {gf.path}; a fix this broad does "
                    "not localise the defect",
                    severity="warn",
                )
            )
    return out


def check_tier_invariants(case: Case) -> TierCheck:
    """Verify a case against its declared tier. Deterministic; never runs the case."""
    declared = case.tier
    if not declared:
        return TierCheck(
            tier="",
            ok=False,
            violations=[TierViolation("tier_missing", "metadata.tier is not set")],
        )
    spec = tier_spec(declared)
    g = case.grader
    v: list[TierViolation] = []

    context_paths = [fb.path for fb in case.files]
    impl_files = [fb for fb in case.files if fb.role in {"impl", "spec"}]
    distractors = [fb for fb in case.files if fb.role == "distractor"]
    hidden_fns = sum(
        count_test_functions(fb.content, language=case.language) for fb in g.hidden_tests
    )
    disclosures = find_disclosures(case.prompt)
    marked = [
        fb.path
        for fb in impl_files
        if find_bug_markers(fb.content, flag_todo=case.task_type == "bugfix")
    ]

    facts = {
        "file_count": len(case.files),
        "distractor_count": len(distractors),
        "hidden_test_files": len(g.hidden_tests),
        "hidden_test_fns": hidden_fns,
        "solution_files": len(g.gold_files),
        "protected_paths": len(g.protected_paths),
        "disclosures": disclosures,
        "bug_marked_files": marked,
    }

    if len(case.files) < spec.min_files:
        v.append(
            TierViolation(
                "too_few_files",
                f"{declared} needs >= {spec.min_files} context files, got {len(case.files)}",
            )
        )
    if spec.max_files is not None and len(case.files) > spec.max_files:
        v.append(
            TierViolation(
                "too_many_files",
                f"{declared} allows <= {spec.max_files} context files, got {len(case.files)}",
            )
        )

    if not spec.allow_disclosure:
        if disclosures:
            v.append(
                TierViolation(
                    "prompt_discloses_defect",
                    f"{declared} requires a symptom-only prompt; disclosed via {disclosures}",
                )
            )
        if marked:
            v.append(
                TierViolation(
                    "stub_has_bug_marker",
                    f"{declared} forbids defect markers in {marked}",
                )
            )

    if hidden_fns < spec.min_hidden_test_fns:
        v.append(
            TierViolation(
                "too_few_hidden_tests",
                f"{declared} needs >= {spec.min_hidden_test_fns} hidden test functions, got {hidden_fns}",
            )
        )
    if len(distractors) < spec.min_distractors:
        v.append(
            TierViolation(
                "too_few_distractors",
                f"{declared} needs >= {spec.min_distractors} files with role=distractor",
            )
        )
    if len(g.gold_files) < spec.min_solution_files:
        v.append(
            TierViolation(
                "too_few_solution_files",
                f"{declared} needs a reference solution touching >= {spec.min_solution_files} "
                f"files, got {len(g.gold_files)}",
            )
        )
    if spec.require_protected_paths and not g.protected_paths:
        v.append(
            TierViolation(
                "protected_paths_required",
                f"{declared} must protect its visible tests via grader.protected_paths",
            )
        )

    for rel in g.protected_paths:
        if rel not in context_paths:
            v.append(
                TierViolation(
                    "protected_path_not_in_context",
                    f"protected path {rel} has no counterpart in context.files",
                )
            )
    for fb in g.hidden_tests:
        if fb.path in context_paths:
            v.append(
                TierViolation(
                    "hidden_test_shadows_context",
                    f"hidden test {fb.path} would overwrite a context file at grading time",
                )
            )
    # A distractor the reference solution has to touch was never a distractor. Nothing else in
    # the case can catch this, because "irrelevant" is not visible in the file's own content.
    solution_paths = {gf.path for gf in g.gold_files}
    for fb in distractors:
        if fb.path in solution_paths:
            v.append(
                TierViolation(
                    "distractor_in_solution",
                    f"{fb.path} is declared role=distractor but the reference solution changes it",
                )
            )

    v.extend(_check_solution_minimality(case))

    if g.hidden_tests and g.mode not in {"script", "composite"}:
        v.append(
            TierViolation(
                "hidden_tests_need_script_grader",
                f"hidden tests are only injected for script/composite graders, got {g.mode!r}",
            )
        )

    # Warnings inform the case author; only errors disqualify a tier. Counting warnings here
    # would make settle_tier downgrade a case over advisory feedback.
    blocking = [x for x in v if x.severity == "error"]
    return TierCheck(tier=declared, ok=not blocking, violations=v, facts=facts)


def axes_for_tier(tier: str) -> list[str]:
    return list(tier_spec(tier).axes)
