"""Label a case with the defect mechanism its stub→gold diff exhibits.

`task_type` is the action (bugfix / feature). This is the *mechanism*: missing CLI wiring,
off-by-one, inverted predicate, and so on. The vocabulary is closed so a case-set distribution
is a real histogram, not a bag of LLM tags.

The classifier reads the reference solution. Symptom-only prompts are a fallback, because
reverse construction deliberately strips mechanism names from the prompt.
"""

from __future__ import annotations

import ast
import difflib
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from aibench.languages import spec_for_path

PROBLEM_TYPES: tuple[str, ...] = (
    "missing_cli_wiring",
    "off_by_one",
    "wrong_predicate",
    "missing_guard",
    "missing_branch",
    "normalize_transform",
    "wrong_path_base",
    "missing_field",
    "missing_symbol",
    "registry_omission",
    "wrong_literal",
    "other",
)

# First match in this order wins when several detectors fire. More specific structural
# patterns outrank "a new function appeared" and "a string changed".
_PRIORITY: tuple[str, ...] = PROBLEM_TYPES

_CLI_HINT = re.compile(
    r"add_argument\s*\(|argparse|ArgumentParser|--[a-z][\w-]*|process\.argv|commander\b",
    re.I,
)
_OFF_BY_ONE = re.compile(
    r"""
    range\s*\([^)]*[+-]\s*1
    | \blen\s*\([^)]+\)\s*-\s*1
    | \[\s*:\s*[^\]]*[+-]\s*1
    """,
    re.I | re.X,
)
_CMP_FLIP = {
    ("<", "<="),
    ("<=", "<"),
    (">", ">="),
    (">=", ">"),
    ("<", ">="),
    (">=", "<"),
    (">", "<="),
    ("<=", ">"),
}
_PRED_FLIP = {("==", "!="), ("!=", "=="), ("and", "or"), ("or", "and"), ("is", "is not")}
_NORMALIZE = re.compile(
    r"\.(strip|lower|upper|replace|split|join|encode|decode|lstrip|rstrip)\s*\("
    r"|re\.sub\s*\(|sanitize_|normali[sz]e_"
)
_PATH_HINT = re.compile(
    r"__file__|os\.path|pathlib|path\.join|dirname\s*\(|\.\./|PACKAGE_ROOT|process\.cwd",
    re.I,
)
_GUARD_RAISE = re.compile(r"\braise\s+\w+|if\s+not\s+|if\s+\w+\s+is\s+None\b")
_JS_DEF = re.compile(
    r"^\s*(?:export\s+)?(?:async\s+)?(?:function|class)\s+([A-Za-z_]\w*)",
    re.M,
)
_WHOLESALE_RATIO = 0.35


@dataclass(frozen=True)
class ProblemTypeResult:
    problem_type: str
    reasons: list[str] = field(default_factory=list)
    source: str = "heuristic"


def stamp_problem_type(case: dict[str, Any]) -> ProblemTypeResult:
    """Write `metadata.problem_type` (and source/reasons). Always succeeds."""
    result = classify_problem_type(case)
    meta = case.setdefault("metadata", {})
    meta["problem_type"] = result.problem_type
    meta["problem_type_source"] = result.source
    meta["problem_type_reasons"] = list(result.reasons)
    return result


def classify_problem_type(case: dict[str, Any] | Any) -> ProblemTypeResult:
    raw = case if isinstance(case, dict) else getattr(case, "raw", None) or {}
    pairs = _impl_gold_pairs(raw)
    if not pairs:
        return _from_prompt(str(raw.get("prompt") or ""), "no_reference_solution")

    hits: dict[str, list[str]] = {k: [] for k in PROBLEM_TYPES}
    min_ratio = 1.0
    for path, stub, gold in pairs:
        min_ratio = min(min_ratio, difflib.SequenceMatcher(None, stub, gold).ratio())
        _score_pair(path, stub, gold, hits)

    ranked = [k for k in _PRIORITY if k != "other" and hits[k]]
    if not ranked:
        return ProblemTypeResult("other", ["no_detector_matched"])
    # A near-total rewrite is not a local mechanism, unless a specific structural detector
    # (CLI flag, comparison flip) still fired on the remnant.
    strong = {
        "missing_cli_wiring",
        "off_by_one",
        "wrong_predicate",
        "missing_guard",
        "wrong_path_base",
        "missing_field",
    }
    if min_ratio < _WHOLESALE_RATIO and not any(h in strong for h in ranked):
        return ProblemTypeResult(
            "other",
            [f"wholesale_rewrite ratio={min_ratio:.2f}"]
            + [f"{k}: {'; '.join(hits[k])}" for k in ranked],
        )
    winner = ranked[0]
    return ProblemTypeResult(winner, hits[winner])


def distribution(cases: list[dict[str, Any]]) -> dict[str, int]:
    """Counts per slug, including `unset` for cases never stamped."""
    counts: Counter[str] = Counter()
    for case in cases:
        meta = case.get("metadata") or {}
        counts[str(meta.get("problem_type") or "unset")] += 1
    return dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))


def _impl_gold_pairs(case: dict[str, Any]) -> list[tuple[str, str, str]]:
    gold_files = ((case.get("grader") or {}).get("gold_files")) or []
    if not gold_files:
        return []
    files = ((case.get("context") or {}).get("files")) or []
    by_path = {str(f.get("path") or ""): f for f in files if f.get("path")}
    out: list[tuple[str, str, str]] = []
    for g in gold_files:
        path = str(g.get("path") or "")
        if not path:
            continue
        stub_entry = by_path.get(path)
        if stub_entry and _skip_file(path, stub_entry.get("role")):
            continue
        stub = str((stub_entry or {}).get("content") or "")
        gold = str(g.get("content") or "")
        if stub == gold:
            continue
        out.append((path, stub, gold))
    return out


def _skip_file(path: str, role: Any) -> bool:
    if role in {"test", "distractor", "spec"}:
        return True
    spec = spec_for_path(path)
    return bool(spec and spec.is_test_path(path))


def _score_pair(path: str, stub: str, gold: str, hits: dict[str, list[str]]) -> None:
    added, removed = _line_delta(stub, gold)
    added_text = "\n".join(added)
    removed_text = "\n".join(removed)

    new_defs = _defined_names(gold) - _defined_names(stub)
    if _CLI_HINT.search(added_text) and not _CLI_HINT.search(removed_text):
        hits["missing_cli_wiring"].append(f"{path}: new CLI surface")

    if _off_by_one(removed, added):
        hits["off_by_one"].append(f"{path}: boundary / ±1")

    if _predicate_flip(removed, added):
        hits["wrong_predicate"].append(f"{path}: condition flipped")

    if (_PATH_HINT.search(added_text) or _PATH_HINT.search(removed_text)) and _path_depth_changed(
        removed_text, added_text
    ):
        hits["wrong_path_base"].append(f"{path}: path resolution base")

    if _is_guard(added, stub, gold):
        hits["missing_guard"].append(f"{path}: new validation / early exit")

    if _is_branch(added, stub, gold):
        hits["missing_branch"].append(f"{path}: new branch")

    if _NORMALIZE.search(added_text) and not _NORMALIZE.search(removed_text):
        hits["normalize_transform"].append(f"{path}: normalize / sanitize")

    new_fields = _ann_fields(gold) - _ann_fields(stub)
    if new_fields:
        hits["missing_field"].append(f"{path}: fields {sorted(new_fields)[:4]}")

    if new_defs and "add_argument" not in added_text:
        hits["missing_symbol"].append(f"{path}: +{sorted(new_defs)[:4]}")

    if (
        not new_defs
        and not new_fields
        and _registry_entries(removed, added)
        and not hits["missing_cli_wiring"]
    ):
        hits["registry_omission"].append(f"{path}: list/map entry")

    if _literal_only(removed, added) and not any(
        hits[k] for k in hits if k not in {"wrong_literal", "other"}
    ):
        hits["wrong_literal"].append(f"{path}: string/number change")


def _line_delta(stub: str, gold: str) -> tuple[list[str], list[str]]:
    matcher = difflib.SequenceMatcher(None, stub.splitlines(), gold.splitlines(), autojunk=False)
    added: list[str] = []
    removed: list[str] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag in {"replace", "delete"}:
            removed.extend(stub.splitlines()[i1:i2])
        if tag in {"replace", "insert"}:
            added.extend(gold.splitlines()[j1:j2])
    return added, removed


def _defined_names(src: str) -> set[str]:
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return set(_JS_DEF.findall(src))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            names.add(node.name)
    return names


def _ann_fields(src: str) -> set[str]:
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return set()
    fields: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        for stmt in node.body:
            if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                fields.add(stmt.target.id)
    return fields


def _off_by_one(removed: list[str], added: list[str]) -> bool:
    if any(_OFF_BY_ONE.search(ln) for ln in added + removed):
        return True
    return _token_pair_flip(removed, added, _CMP_FLIP)


def _predicate_flip(removed: list[str], added: list[str]) -> bool:
    if _token_pair_flip(removed, added, _PRED_FLIP):
        return True
    # `return not x` ↔ `return x`, or a leading `not ` added/removed on the same skeleton.
    for r, a in _paired_changed_lines(removed, added):
        rs, al = r.strip(), a.strip()
        if rs.startswith("not ") and rs[4:] == al:
            return True
        if al.startswith("not ") and al[4:] == rs:
            return True
        if re.sub(r"\bnot\b\s*", "", rs) == re.sub(r"\bnot\b\s*", "", al) and (
            ("not" in rs) != ("not" in al)
        ):
            return True
    return False


def _token_pair_flip(removed: list[str], added: list[str], pairs: set[tuple[str, str]]) -> bool:
    for r, a in _paired_changed_lines(removed, added):
        rt, at = _cmp_tokens(r), _cmp_tokens(a)
        if rt and at and (rt, at) in pairs:
            return True
    return False


def _cmp_tokens(line: str) -> str | None:
    for tok in ("<=", ">=", "==", "!=", "is not", "and", "or", "<", ">", "is"):
        if re.search(rf"(?<![\w!]){re.escape(tok)}(?![\w=])", line):
            return tok
    return None


def _paired_changed_lines(removed: list[str], added: list[str]) -> list[tuple[str, str]]:
    if len(removed) == 1 and len(added) == 1:
        return [(removed[0], added[0])]
    out: list[tuple[str, str]] = []
    used: set[int] = set()
    for r in removed:
        best_j, best = -1, 0.0
        for j, a in enumerate(added):
            if j in used:
                continue
            ratio = difflib.SequenceMatcher(None, r.strip(), a.strip()).ratio()
            if ratio > best:
                best, best_j = ratio, j
        if best >= 0.6 and best_j >= 0:
            used.add(best_j)
            out.append((r, added[best_j]))
    return out


def _path_depth_changed(removed: str, added: str) -> bool:
    def depth(s: str) -> int:
        return s.count("../") + s.count("..\\") + len(re.findall(r"dirname\s*\(", s))

    return depth(removed) != depth(added) or (
        ("__file__" in added) != ("__file__" in removed)
        or ("PACKAGE_ROOT" in added) != ("PACKAGE_ROOT" in removed)
    )


def _is_guard(added: list[str], stub: str, gold: str) -> bool:
    if any("encoding=" in ln and "encoding=" not in stub for ln in added):
        return True
    if any(any(k in ln for k in ("elif ", "else:", "else if", "case ")) for ln in added):
        return False
    if not any(_GUARD_RAISE.search(ln) for ln in added):
        return False
    # A guard is an early exit added to a function that already existed.
    return not (_defined_names(gold) - _defined_names(stub))


def _is_branch(added: list[str], stub: str, gold: str) -> bool:
    keywords = ("elif ", "else:", "case ", "default:", "else if")
    if not any(any(k in ln for k in keywords) for ln in added):
        # A new `if` that is not a guard (already handled) and not a new function body.
        new_ifs = [ln for ln in added if re.search(r"^\s*if\s+", ln)]
        if not new_ifs:
            return False
        if _defined_names(gold) - _defined_names(stub):
            return False
        return not _is_guard(added, stub, gold)
    return True


def _registry_entries(removed: list[str], added: list[str]) -> bool:
    entry = re.compile(r"""^\s*["'][\w./-]+["']\s*:|^\s*["'][\w./-]+["']\s*,?\s*$""")
    return any(entry.search(ln) for ln in added) and not any(entry.search(ln) for ln in removed)


def _literal_only(removed: list[str], added: list[str]) -> bool:
    if not removed or not added:
        return False
    if len(removed) > 6 or len(added) > 6:
        return False
    for r, a in _paired_changed_lines(removed, added):
        if _strip_literals(r) == _strip_literals(a) and r.strip() != a.strip():
            return True
    return False


def _strip_literals(line: str) -> str:
    line = re.sub(r"""(['"])(?:\\.|(?!\1).)*\1""", "''", line)
    line = re.sub(r"\b\d+(?:\.\d+)?\b", "0", line)
    return re.sub(r"\s+", "", line)


_PROMPT_HINTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("off_by_one", ("off-by-one", "off by one", "少算", "多算", "边界")),
    ("missing_guard", ("空列表", "empty list", "zerodivision", "none check", "校验")),
    ("wrong_predicate", ("inverted", "写反", "条件反了", "or vs and")),
    ("normalize_transform", ("sanitize", "normalize", "规范化", "清洗")),
    ("missing_cli_wiring", ("命令行", "cli", "flag", "参数没接")),
)


def _from_prompt(prompt: str, why: str) -> ProblemTypeResult:
    p = prompt.lower()
    for slug, keys in _PROMPT_HINTS:
        if any(k.lower() in p for k in keys):
            return ProblemTypeResult(slug, [why, f"prompt:{slug}"])
    return ProblemTypeResult("other", [why])
