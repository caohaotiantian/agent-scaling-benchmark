"""Label a case with the defect mechanism its stub→gold diff exhibits.

`task_type` is the action (bugfix / feature). This is the *mechanism*. The vocabulary is
closed so a case-set distribution is a real histogram.

No model is called. Detectors read the reference-solution diff; the prompt is a fallback
only when there is no gold. Pairwise cases are labelled from `task_type`, not the CHOICE
literal.
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
    "review_choice",
    "missing_cli_wiring",
    "wrong_condition",
    "control_flow",
    "normalize_transform",
    "wrong_path_base",
    "schema_gap",
    "missing_symbol",
    "copy_change",
    "wrong_literal",
    "rewrite",
    "other",
)

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
_PRED_FLIP = {
    ("==", "!="),
    ("!=", "=="),
    ("and", "or"),
    ("or", "and"),
    ("is", "is not"),
    ("is not", "is"),
}
_NORMALIZE = re.compile(
    r"\.(strip|lower|upper|replace|split|join|encode|decode|lstrip|rstrip)\s*\("
    r"|re\.sub\s*\(|sanitize_|normali[sz]e_"
)
_PATH_HINT = re.compile(
    r"__file__|os\.path|pathlib|path\.join|dirname\s*\(|\.\./|PACKAGE_ROOT|process\.cwd|"
    r"""['"]\.\.['"]""",
    re.I,
)
_GUARD_RAISE = re.compile(r"\braise\s+\w+|if\s+not\s+|if\s+\w+\s+is\s+None\b")
_JS_DEF = re.compile(
    r"^\s*(?:export\s+)?(?:async\s+)?(?:function|class)\s+([A-Za-z_]\w*)",
    re.M,
)
_JS_METHOD = re.compile(
    r"^\s*(?:async\s+)?([A-Za-z_]\w*)\s*\([^()]*\)\s*\{",
    re.M,
)
_JS_METHOD_SKIP = frozenset({"if", "for", "while", "switch", "catch", "function", "else"})
_KEY_LINE = re.compile(r"""^\s*['"]([\w./-]+)['"]\s*:""")
_LIST_ENTRY = re.compile(r"""^\s*['"]([\w./-]+)['"]\s*,?\s*$""")
_TS_UNION = re.compile(r"""\|\s*['"](\w+)['"]|['"](\w+)['"]\s*\|""")
_CONST_ASSIGN = re.compile(r"^([A-Z][A-Z0-9_]*)\s*=", re.M)
_CJK = re.compile(r"[\u4e00-\u9fff]")
_STRINGY = re.compile(r"""(['"]{1,3}).+\1""")
_WHOLESALE_RATIO = 0.35
_REVIEW_GENERATION = frozenset({"review", "review-choice"})


@dataclass(frozen=True)
class ProblemTypeResult:
    problem_type: str
    reasons: list[str] = field(default_factory=list)
    source: str = "heuristic"


def stamp_problem_type(case: dict[str, Any]) -> ProblemTypeResult:
    """Write `metadata.problem_type` (and source/reasons). Always succeeds."""
    try:
        result = classify_problem_type(case)
    except Exception as e:
        result = ProblemTypeResult("other", [f"classify_error:{type(e).__name__}"])
    if not isinstance(case, dict):
        return result
    meta = case.get("metadata")
    if not isinstance(meta, dict):
        meta = {}
        case["metadata"] = meta
    meta["problem_type"] = result.problem_type
    meta["problem_type_source"] = result.source
    meta["problem_type_reasons"] = list(result.reasons)
    return result


def classify_problem_type(case: dict[str, Any] | Any) -> ProblemTypeResult:
    raw = case if isinstance(case, dict) else getattr(case, "raw", None)
    if not isinstance(raw, dict):
        return ProblemTypeResult("other", ["malformed_case"])
    if str(raw.get("task_type") or "") == "pairwise":
        return ProblemTypeResult("review_choice", ["task_type=pairwise"])
    meta = raw.get("metadata") if isinstance(raw.get("metadata"), dict) else {}
    if str(meta.get("generation") or "") in _REVIEW_GENERATION:
        return ProblemTypeResult("review_choice", ["generation=review"])

    try:
        pairs = _impl_gold_pairs(raw)
    except (TypeError, AttributeError):
        return ProblemTypeResult("other", ["malformed_workspace"])
    if not pairs:
        return _from_prompt(str(raw.get("prompt") or ""), "no_reference_solution")

    hits: dict[str, list[str]] = {k: [] for k in PROBLEM_TYPES}
    min_ratio = 1.0
    for path, stub, gold in pairs:
        min_ratio = min(min_ratio, difflib.SequenceMatcher(None, stub, gold).ratio())
        _score_pair(path, stub, gold, hits)

    ranked = [k for k in _PRIORITY if k not in {"other", "rewrite", "review_choice"} and hits[k]]
    stub_empty = all(not stub.strip() for _path, stub, _gold in pairs)
    strong = {
        "missing_cli_wiring",
        "wrong_condition",
        "wrong_path_base",
        "schema_gap",
        "control_flow",
    }
    if min_ratio < _WHOLESALE_RATIO and not stub_empty and not any(h in strong for h in ranked):
        return ProblemTypeResult(
            "rewrite",
            [f"wholesale_rewrite ratio={min_ratio:.2f}"]
            + [f"{k}: {'; '.join(hits[k])}" for k in ranked],
        )
    if not ranked:
        return ProblemTypeResult("other", ["no_detector_matched"])
    winner = ranked[0]
    return ProblemTypeResult(winner, hits[winner])


def distribution(cases: list[dict[str, Any]]) -> dict[str, int]:
    """Counts per slug, including `unknown` for cases never stamped."""
    counts: Counter[str] = Counter()
    for case in cases:
        meta = case.get("metadata") if isinstance(case, dict) else None
        if not isinstance(meta, dict):
            counts["unknown"] += 1
            continue
        counts[str(meta.get("problem_type") or "unknown")] += 1
    return dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))


def _file_dicts(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    out: list[dict[str, Any]] = []
    for item in value:
        if isinstance(item, dict) and str(item.get("path") or "").strip():
            out.append(item)
    return out


def _impl_gold_pairs(case: dict[str, Any]) -> list[tuple[str, str, str]]:
    grader = case.get("grader")
    ctx = case.get("context")
    gold_files = _file_dicts(grader.get("gold_files") if isinstance(grader, dict) else None)
    if not gold_files:
        return []
    files = _file_dicts(ctx.get("files") if isinstance(ctx, dict) else None)
    by_path = {str(f.get("path") or ""): f for f in files}
    out: list[tuple[str, str, str]] = []
    for g in gold_files:
        path = str(g.get("path") or "")
        stub_entry = by_path.get(path)
        role = stub_entry.get("role") if stub_entry else None
        if _skip_file(path, role):
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

    if _off_by_one(removed, added) or _predicate_flip(removed, added):
        hits["wrong_condition"].append(f"{path}: comparison / boolean / ±1")

    if (_PATH_HINT.search(added_text) or _PATH_HINT.search(removed_text)) and _path_depth_changed(
        removed_text, added_text
    ):
        hits["wrong_path_base"].append(f"{path}: path resolution base")

    if _is_guard(added, stub, gold) or _is_branch(added, stub, gold):
        hits["control_flow"].append(f"{path}: guard or branch")

    if _NORMALIZE.search(added_text) and not _NORMALIZE.search(removed_text):
        hits["normalize_transform"].append(f"{path}: normalize / sanitize")

    gap = _schema_keys(gold) - _schema_keys(stub)
    if not gap:
        gap = _schema_keys("\n".join(added)) - _schema_keys("\n".join(removed))
    if gap:
        hits["schema_gap"].append(f"{path}: +{sorted(gap)[:6]}")

    if new_defs and "add_argument" not in added_text:
        hits["missing_symbol"].append(f"{path}: +{sorted(new_defs)[:4]}")

    if _is_copy_change(removed, added):
        hits["copy_change"].append(f"{path}: user-facing / i18n text")

    if _literal_only(removed, added) and not any(
        hits[k] for k in hits if k not in {"wrong_literal", "other", "rewrite", "review_choice"}
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
    names: set[str] = set()
    try:
        tree = ast.parse(src)
    except SyntaxError:
        names.update(_JS_DEF.findall(src))
        for name in _JS_METHOD.findall(src):
            if name not in _JS_METHOD_SKIP:
                names.add(name)
        names.update(_CONST_ASSIGN.findall(src))
        return names
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            names.add(node.name)
    names.update(_CONST_ASSIGN.findall(src))
    return names


def _schema_keys(src: str) -> set[str]:
    keys: set[str] = set()
    try:
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                for stmt in node.body:
                    if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                        keys.add(stmt.target.id)
            if isinstance(node, ast.Dict):
                for k in node.keys:
                    if isinstance(k, ast.Constant) and isinstance(k.value, str):
                        keys.add(k.value)
            if isinstance(node, ast.List):
                for elt in node.elts:
                    if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                        keys.add(elt.value)
    except SyntaxError:
        pass
    for ln in src.splitlines():
        if m := _KEY_LINE.match(ln):
            keys.add(m.group(1))
        if m := _LIST_ENTRY.match(ln):
            keys.add(m.group(1))
        for m in _TS_UNION.finditer(ln):
            keys.add(next(g for g in m.groups() if g))
    keys.update(_CONST_ASSIGN.findall(src))
    return keys


def _off_by_one(removed: list[str], added: list[str]) -> bool:
    if any(_OFF_BY_ONE.search(ln) for ln in added + removed):
        return True
    return _token_pair_flip(removed, added, _CMP_FLIP)


def _predicate_flip(removed: list[str], added: list[str]) -> bool:
    if _token_pair_flip(removed, added, _PRED_FLIP):
        return True
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
        return (
            s.count("../")
            + s.count("..\\")
            + len(re.findall(r"""['"]\.\.['"]""", s))
            + len(re.findall(r"dirname\s*\(", s))
        )

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
    return not (_defined_names(gold) - _defined_names(stub))


def _is_branch(added: list[str], stub: str, gold: str) -> bool:
    if _defined_names(gold) - _defined_names(stub):
        return False
    keywords = ("elif ", "else:", "case ", "default:", "else if")
    if not any(any(k in ln for k in keywords) for ln in added):
        new_ifs = [ln for ln in added if re.search(r"^\s*if\s+", ln)]
        if not new_ifs:
            return False
        return not _is_guard(added, stub, gold)
    return True


def _is_copy_change(removed: list[str], added: list[str]) -> bool:
    lines = [
        ln
        for ln in removed + added
        if ln.strip() and not ln.strip().startswith(("#", "//", "*", "/*"))
    ]
    if not lines:
        return False
    cjk = any(_CJK.search(ln) for ln in lines)
    stringy = sum(
        1 for ln in lines if _STRINGY.search(ln) or _CJK.search(ln) or '"""' in ln or "'''" in ln
    )
    # A one-line constant swap is `wrong_literal`. Copy/i18n rewrites many user-facing strings.
    if cjk:
        return stringy / len(lines) >= 0.5
    return len(lines) >= 4 and stringy / len(lines) >= 0.7


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
    ("wrong_condition", ("off-by-one", "off by one", "少算", "多算", "边界", "inverted", "写反")),
    ("control_flow", ("空列表", "empty list", "zerodivision", "none check", "校验")),
    ("normalize_transform", ("sanitize", "normalize", "规范化", "清洗")),
    ("missing_cli_wiring", ("命令行", "--flag", "参数没接")),
    ("copy_change", ("中文", "i18n", "文案", "prompt")),
)


def _from_prompt(prompt: str, why: str) -> ProblemTypeResult:
    p = prompt.lower()
    for slug, keys in _PROMPT_HINTS:
        if any(re.search(rf"(?<![a-z0-9_]){re.escape(k.lower())}(?![a-z0-9_])", p) for k in keys):
            return ProblemTypeResult(slug, [why, f"prompt:{slug}"])
    return ProblemTypeResult("other", [why])
