#!/usr/bin/env python3
"""Why the always-failed cases failed — read off an ablation that already ran.

`HANDOFF §0.-1` measured `_clean2026` at 3 of 45 discordant against a self-flip floor of 8.9%
in both models, and left one question open: 27 of the 45 defeat both models, and a pass rate is
silent about *why*. Two explanations survive that number and they point opposite ways. If those
27 fail on behaviour the solver got wrong, the cases are hard and the set is simply too small.
If they fail because the grader demands something the solver had no way to know, the block is in
case construction, and no amount of extra cases from this corpus fixes it.

The distinction is machine-checkable and costs nothing: every run already wrote `results.jsonl`.

Three readings are produced.

**Mechanism** comes from `grade.detail`. The `failure_category` field cannot do this job — on
this run 170 of the 175 failures carry the same fallback string — so the pytest/node output is
classified instead, with `collection_error` and `test_pass_ratio` (both computed by the grader
from untruncated output) preferred wherever they are decisive.

**Inferability** extends the rule `validity.check_hidden_tests_are_inferable` already enforces:
the interface must be visible even when the behaviour is hidden. That check reads
`from X import n`, JS named imports, and `module.attr`. It does not read *keyword argument
names*, and a keyword argument is interface — `discover_config_files(tmp, extra_skip="custom")`
cannot be answered by a solver who has never seen the string `extra_skip`, which is exactly the
shape §14.3.8 was written to reject.

**Data-only defects** are the other half of the same problem seen from the gold patch: when the
fix changes nothing inside any function or class, the answer is a literal value — a manifest
entry, a config number — that the grader checks exactly and no prompt can convey without giving
it away.

All three are cross-tabulated against the three outcome groups, because concentration is the
claim being tested. A defect spread evenly across solved and unsolved cases explains nothing
about the unsolved ones, which is why the `both_pass` column is printed beside every signal.

Usage:

    uv run python scripts/discrimination_diagnostic.py \\
      --run runs/ablation_20260814_111227 --case-set _clean2026 --self-check
"""

from __future__ import annotations

import argparse
import ast
import difflib
import json
import math
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from aibench.cases import load_cases
from aibench.models import Case
from aibench.validity import _defines, required_hidden_symbols

#: Published in `HANDOFF §0.-1` from this run, before this script existed. They are the only
#: independent check available on the parsing below: a grouping bug that miscounts a case would
#: have to miscount it into exactly the published totals to survive.
PUBLISHED = {
    "run_dir": "runs/ablation_20260814_111227",
    "case_set": "_clean2026",
    "case_count": 45,
    "passes_by_run": {
        "bare-glm5-r1": 16,
        "bare-glm5-r2": 18,
        "bare-glm5-r3": 16,
        "bare-glm52-r1": 13,
        "bare-glm52-r2": 15,
        "bare-glm52-r3": 17,
    },
    # r1 against r1: 3 discordant, all three in favour of GLM-5, McNemar p = 0.25.
    "r1_discordant": 3,
    "r1_only_glm5": 3,
    "r1_only_glm52": 0,
    # Cases where one model's three repeats are not unanimous. 4/45 = 8.9%.
    "self_flip": {"GLM-5": 4, "GLM-5.2": 4},
    # "27 of 45 defeat both models, 15 defeat neither, and 3 separate them" — the partition is
    # by each model's majority over its three repeats. `any of 3` gives 25/15/5 and `all of 3`
    # gives 30/12/3, so the choice is not free. It is *not* unique, though: the r2 repeat pair
    # alone also gives 27/15/3 (r1 gives 29/13/3, r3 gives 26/14/5), and an earlier version of
    # this comment claimed uniqueness by listing r1 and r3 and omitting r2. Majority is the one
    # used because it spends all three repeats, not because it is the only one that fits.
    "groups": {"both_fail": 27, "both_pass": 15, "discordant": 3},
}


# --------------------------------------------------------------------------------------
# Loading
# --------------------------------------------------------------------------------------


@dataclass
class RunRows:
    run_id: str
    model: str
    rows: dict[str, dict[str, Any]]


def load_runs(run_dir: Path) -> list[RunRows]:
    """One entry per experiment directory, rows keyed by case_id.

    Keyed rather than listed so a duplicated case_id raises here instead of quietly
    double-counting downstream — the failure mode `§5.8` records three times.
    """
    runs: list[RunRows] = []
    for sub in sorted(run_dir.iterdir()):
        manifest = sub / "run_manifest.json"
        results = sub / "results.jsonl"
        if not (manifest.is_file() and results.is_file()):
            continue
        meta = json.loads(manifest.read_text(encoding="utf-8"))
        rows: dict[str, dict[str, Any]] = {}
        for line in results.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            cid = row["case_id"]
            if cid in rows:
                raise ValueError(f"{results}: duplicate case_id {cid}")
            rows[cid] = row
        runs.append(RunRows(run_id=meta["run_id"], model=meta["main_model"], rows=rows))
    if not runs:
        raise FileNotFoundError(f"no experiment directories under {run_dir}")
    return runs


# --------------------------------------------------------------------------------------
# Grouping
# --------------------------------------------------------------------------------------


@dataclass
class CaseOutcome:
    case_id: str
    by_model: dict[str, list[bool]] = field(default_factory=dict)

    @property
    def all_outcomes(self) -> list[bool]:
        return [p for passes in self.by_model.values() for p in passes]

    @property
    def group(self) -> str:
        """The published partition: each model's majority over its own repeats, then compared.

        Of the four ways to collapse three repeats into one verdict, this is the one that
        reproduces `HANDOFF §0.-1`'s 27 / 15 / 3 — see `PUBLISHED["groups"]`. Choosing any other
        would silently renumber every figure quoted downstream.
        """
        verdicts = {m: self.majority(m) for m in self.by_model}
        if all(verdicts.values()):
            return "both_pass"
        if not any(verdicts.values()):
            return "both_fail"
        return "discordant"

    @property
    def unanimity(self) -> str:
        """Whether the six runs agree, ignoring which model produced them."""
        outcomes = self.all_outcomes
        if all(outcomes):
            return "never_failed"
        if not any(outcomes):
            return "never_passed"
        return "split"

    def majority(self, model: str) -> bool:
        passes = self.by_model[model]
        return sum(passes) * 2 > len(passes)

    def self_flips(self, model: str) -> bool:
        passes = self.by_model[model]
        return 0 < sum(passes) < len(passes)


def outcomes_by_case(runs: list[RunRows], case_ids: list[str]) -> dict[str, CaseOutcome]:
    out: dict[str, CaseOutcome] = {cid: CaseOutcome(cid) for cid in case_ids}
    for run in runs:
        missing = set(case_ids) - set(run.rows)
        if missing:
            raise ValueError(
                f"{run.run_id} is missing {len(missing)} cases, e.g. {sorted(missing)[:3]}"
            )
        for cid in case_ids:
            out[cid].by_model.setdefault(run.model, []).append(bool(run.rows[cid]["passed"]))
    return out


# --------------------------------------------------------------------------------------
# Mechanism
# --------------------------------------------------------------------------------------

#: Ordered: the first pattern that matches wins, so the specific interface errors are tried
#: before the generic ones. A test that never ran is decided by the grader's own
#: `collection_error` flag rather than by any of these, because that flag was computed from
#: output this field has only the last 1500 bytes of.
_MECHANISMS: list[tuple[str, re.Pattern[str]]] = [
    ("missing_kwarg", re.compile(r"TypeError: .*unexpected keyword argument")),
    ("wrong_arity", re.compile(r"TypeError: .*(takes|missing|positional argument)")),
    ("missing_attr", re.compile(r"AttributeError: .*has no attribute")),
    ("missing_name", re.compile(r"NameError: name|ImportError: cannot import name")),
    ("assertion", re.compile(r"AssertionError|assert\.|AssertionError:|ERR_ASSERTION")),
]

#: A workspace the solver left unparseable. Distinct from a case that arrives broken: the
#: `case_contains_tool_output_footer` gate and `audit-cases` both passed on this set, so a
#: syntax error at grading time was written by the model.
_SOLVER_BROKE_FILE = re.compile(r"SyntaxError|unterminated|Unexpected token|unexpected EOF")


def classify(row: dict[str, Any]) -> str:
    detail = row.get("grade", {}).get("detail") or ""
    if row.get("infra_error"):
        return "infra_error"
    if row.get("reward_hack"):
        return "reward_hack"
    if row.get("collection_error"):
        return "solver_broke_file" if _SOLVER_BROKE_FILE.search(detail) else "collection_error"
    for name, pattern in _MECHANISMS:
        if pattern.search(detail):
            return name
    if not detail:
        return "no_detail"
    return "other_error"


#: pytest's short summary, then node's test runner. A first version had only the pytest form,
#: which does not appear in node output at all — so all three JavaScript cases came back with an
#: empty set and were reported as "the tail truncated the summary away". One of their details is
#: 547 bytes against a 1500-byte cap, so truncation was never the reason. This is the shape the
#: script is built to catch: a predicate that silently returns nothing for a whole class of input.
_FAILED_LINE = re.compile(r"^(?:FAILED|ERROR)\s+(\S+)", re.M)
_NODE_FAILED_LINE = re.compile(r"^\s*[✖✗]\s+(.+?)(?:\s+\(\d+(?:\.\d+)?ms\))?\s*$", re.M)


def failing_tests(row: dict[str, Any]) -> frozenset[str]:
    """Test ids named in the runner's failure summary, for pytest and for node."""
    detail = row.get("grade", {}).get("detail") or ""
    return frozenset(_FAILED_LINE.findall(detail)) | frozenset(_NODE_FAILED_LINE.findall(detail))


# --------------------------------------------------------------------------------------
# Inferability: what the hidden tests demand, and whether the solver could see it
# --------------------------------------------------------------------------------------


def _impl_module_names(case: Case) -> set[str]:
    names = set()
    for fb in case.files:
        if fb.role == "impl":
            base = fb.path.replace("\\", "/").rsplit("/", 1)[-1]
            names.add(base.rsplit(".", 1)[0])
    return names


def _calls_into_impl(tree: ast.AST, modules: set[str]) -> list[ast.Call]:
    """Calls that reach the implementation, however the test spells the path to it.

    Resolving the callee rather than matching text is what keeps `os.makedirs(p,
    exist_ok=True)` in a fixture helper out of the interface — `exist_ok` is the standard
    library's parameter, not the case's.

    Four spellings reach it, and a first version of this function saw only two:

    * `impl.f(...)` after `import impl`, including under an alias;
    * `f(...)` after `from impl import f`;
    * `obj.method(...)` where `obj` came from an implementation constructor —
      `r = rank.Rank({}); r.score(..., iteration_no=5)` is how `rev-a60dac9e85e808bf` demands
      `iteration_no`, and the first version returned nothing for it;
    * `impl.Cls().method(...)`, with no intermediate name.

    Only a CapWords callee binds an object. `result = impl.discover(...)` followed by
    `result.get(x, default=1)` would otherwise put `dict.get`'s parameter into the case's
    interface, and a check that reports the standard library reports everything.
    """
    roots: set[str] = set(modules)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(a.asname or a.name for a in node.names if a.name in modules)
        elif (
            isinstance(node, ast.ImportFrom)
            and node.module
            and node.module.rsplit(".", 1)[-1] in modules
        ):
            roots.update(a.asname or a.name for a in node.names)

    def rooted(node: ast.expr) -> bool:
        if isinstance(node, ast.Name):
            return node.id in roots
        if isinstance(node, ast.Attribute):
            return rooted(node.value)
        if isinstance(node, ast.Call):
            return rooted(node.func)
        return False

    def constructs(node: ast.expr) -> bool:
        if not isinstance(node, ast.Call) or not rooted(node.func):
            return False
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
        return bool(name[:1].isupper())

    # Two passes: an object bound on one line is called on a later one, and `ast.walk` makes no
    # promise about source order.
    for _ in range(2):
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and constructs(node.value):
                roots.update(t.id for t in node.targets if isinstance(t, ast.Name))

    return [n for n in ast.walk(tree) if isinstance(n, ast.Call) and rooted(n.func)]


def hidden_call_keywords(case: Case) -> dict[str, set[str]]:
    """Keyword argument names each hidden test passes to the implementation, by test path.

    Python only. JavaScript has no keyword arguments, so there is nothing to miss there; a
    parse failure is recorded rather than dropped.
    """
    modules = _impl_module_names(case)
    out: dict[str, set[str]] = {}
    for fb in case.grader.hidden_tests:
        if not fb.path.endswith(".py"):
            continue
        try:
            tree = ast.parse(fb.content or "")
        except SyntaxError:
            out.setdefault(fb.path, set())
            continue
        found = {
            kw.arg for call in _calls_into_impl(tree, modules) for kw in call.keywords if kw.arg
        }
        if found:
            out[fb.path] = found
    return out


#: Short or purely structural literals carry no specification — `""`, `"/"`, `"a"` are fixture
#: plumbing, and flagging them would drown the signal this is looking for.
_TRIVIAL_LITERAL = re.compile(r"^[\W_]*$")


def hidden_assert_literals(case: Case) -> dict[str, set[str]]:
    """String literals a hidden test asserts on that the test does not itself establish.

    Weaker than :func:`hidden_call_keywords`, and reported separately for that reason. A hidden
    test may legitimately assert on a string the solver must produce — that is behaviour, and
    hiding behaviour is the point. It becomes unanswerable only when the exact token is not
    derivable: `assert "JDK" in captured.out` against a stub that prints a different sentence
    asks the solver to guess a word.

    Two kinds of literal are excluded because they are not demands on the solver:

    * arguments the test passes *into* the implementation — `discover('/tmp')` states an input,
      not an expectation;
    * literals the test file establishes elsewhere — fixture data it wrote and then read back,
      which the solver can follow out of the test setup.
    """
    modules = _impl_module_names(case)
    out: dict[str, set[str]] = {}
    for fb in case.grader.hidden_tests:
        if not fb.path.endswith(".py"):
            continue
        content = fb.content or ""
        try:
            tree = ast.parse(content)
        except SyntaxError:
            continue
        # `node.test` only. `assert x, "Should have a search_articles method"` puts a sentence
        # in `node.msg` that describes the failure rather than specifying anything, and counting
        # those made this heuristic fire on two thirds of the cases both models solve.
        #
        # `self.assertIn("JDK", out)` is the same statement with no `Assert` node behind it. All
        # 37 Python hidden tests in `_clean2026` use the bare form, so this arm changes nothing
        # here — but without it a unittest-style set would return a clean negative meaning "not
        # checked", which is the failure shape this script exists to catch.
        in_assert: set[int] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Assert):
                in_assert.update(id(n) for n in ast.walk(node.test))
            elif (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr.startswith("assert")
            ):
                in_assert.update(id(n) for arg in node.args for n in ast.walk(arg))
        inputs = {
            id(n)
            for call in _calls_into_impl(tree, modules)
            for arg in [*call.args, *(kw.value for kw in call.keywords)]
            for n in ast.walk(arg)
        }
        asserted: set[str] = set()
        elsewhere: set[str] = set()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
                continue
            value = node.value.strip()
            if len(value) < 3 or _TRIVIAL_LITERAL.match(value):
                continue
            if id(node) in in_assert and id(node) not in inputs:
                asserted.add(value)
            else:
                elsewhere.add(value)
        remaining = asserted - elsewhere
        if remaining:
            out[fb.path] = remaining
    return out


def visible_surface(case: Case) -> str:
    """Everything the solver can read: shipped files, visible tests, and the prompt.

    Deliberately excludes the gold files. They are where the missing name is guaranteed to
    appear — that is what makes the case pass its solvability gate — and they are exactly what
    the solver never sees.
    """
    return "\n".join([fb.content or "" for fb in case.files] + [case.prompt or ""])


def _gold_changes(case: Case) -> tuple[list[ast.AST], bool] | None:
    """The gold AST nodes on lines the patch changed, and whether all of them are module data.

    ``None`` when no verdict can be reached: a non-Python file, an unparseable gold, or a gold
    identical to the stub. Reported as unknown rather than folded into either answer.
    """
    nodes: list[ast.AST] = []
    data_only = True
    stubs = {fb.path: fb.content or "" for fb in case.files if fb.role == "impl"}
    seen = False
    for gold in case.grader.gold_files:
        stub = stubs.get(gold.path)
        if stub is None or not gold.path.endswith(".py"):
            return None
        text = gold.content or ""
        try:
            tree = ast.parse(text)
        except SyntaxError:
            return None
        # `decorator_list` sits above `lineno`, which points at the `def`. A route or a
        # `@property` swapped on a decorator is behaviour, and without this the span misses it.
        spans = []
        for n in ast.walk(tree):
            if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                start = min([n.lineno, *(d.lineno for d in n.decorator_list)])
                spans.append((start, n.end_lineno or n.lineno))
        stub_lines, gold_lines = stub.splitlines(), text.splitlines()
        changed: set[int] = set()
        deleted_from_body = False
        for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(
            None, stub_lines, gold_lines
        ).get_opcodes():
            if tag == "equal":
                continue
            # A pure delete has j1 == j2, so the gold-side range is empty and the change would
            # vanish. Deleting a branch out of a function is behaviour; look at the stub side.
            if j1 == j2:
                deleted_from_body = deleted_from_body or any(
                    line.startswith((" ", "\t")) for line in stub_lines[i1:i2] if line.strip()
                )
            changed.update(range(j1 + 1, j2 + 1))
        if not changed and not deleted_from_body:
            return None
        seen = True
        if deleted_from_body or any(s <= n <= e for n in changed for s, e in spans):
            data_only = False
        nodes.extend(n for n in ast.walk(tree) if getattr(n, "lineno", None) in changed)
    return (nodes, data_only) if seen else None


def defect_is_data_only(case: Case) -> bool | None:
    """True when the gold patch touches nothing inside a function or class body.

    A defect that lives entirely in module-level data is a *value*, not a behaviour: an Odoo
    `__manifest__.py` whose `data` list is missing `views/ai_config_views.xml`, a settings module
    whose push length should be 800 rather than 500.

    Structural only. Whether the solver could have known the value is a separate question and a
    separate function — three of the eight cases this flags in `_clean2026` have the value
    written out in the prompt.
    """
    result = _gold_changes(case)
    return None if result is None else result[1]


def data_defect_is_unspecified(case: Case) -> bool | None:
    """A data-only defect whose new value appears nowhere the solver can read.

    The pairing matters. `defect_is_data_only` alone says the grader checks an exact literal;
    it does not say the literal was withheld. `rev-85d5e687ce9f7d51` writes the two numbers and
    all four field names into the prompt, and both models still failed it six times out of six —
    that is evidence about the models, not about the case, and counting it as unanswerable would
    have inflated the headline by three cases.
    """
    result = _gold_changes(case)
    if result is None:
        return None
    nodes, data_only = result
    if not data_only:
        return False
    surface = visible_surface(case)
    for node in nodes:
        if not isinstance(node, ast.Constant):
            continue
        value = node.value
        if isinstance(value, bool) or not isinstance(value, str | int | float):
            continue
        # A number needs a word boundary — `2` is a substring of almost any prompt. A string is
        # matched whole, because that is the form the grader compares.
        if value not in surface if isinstance(value, str) else not _defines(surface, str(value)):
            return True
    return False


@dataclass
class Inferability:
    unknowable_symbols: dict[str, list[str]] = field(default_factory=dict)
    unknowable_kwargs: dict[str, list[str]] = field(default_factory=dict)
    unknowable_literals: dict[str, list[str]] = field(default_factory=dict)

    @property
    def clean(self) -> bool:
        """Interface only. The literal check is a heuristic and is reported on its own."""
        return not self.unknowable_symbols and not self.unknowable_kwargs


def inferability(case: Case) -> Inferability:
    surface = visible_surface(case)
    result = Inferability()
    for path, names in required_hidden_symbols(case).items():
        missing = sorted(n for n in names if not _defines(surface, n))
        if missing:
            result.unknowable_symbols[path] = missing
    for path, kwargs in hidden_call_keywords(case).items():
        missing = sorted(k for k in kwargs if not _defines(surface, k))
        if missing:
            result.unknowable_kwargs[path] = missing
    for path, literals in hidden_assert_literals(case).items():
        missing = sorted(lit for lit in literals if lit not in surface)
        if missing:
            result.unknowable_literals[path] = missing
    return result


# --------------------------------------------------------------------------------------
# Self-check
# --------------------------------------------------------------------------------------


def self_check(runs: list[RunRows], outcomes: dict[str, CaseOutcome]) -> list[str]:
    """Re-derive the published figures. Any mismatch is a parsing bug in this script."""
    failures: list[str] = []

    if len(outcomes) != PUBLISHED["case_count"]:
        failures.append(f"case count {len(outcomes)} != {PUBLISHED['case_count']}")

    for run in runs:
        want = PUBLISHED["passes_by_run"].get(run.run_id)
        got = sum(bool(r["passed"]) for r in run.rows.values())
        if want is None:
            failures.append(f"unexpected run_id {run.run_id}")
        elif got != want:
            failures.append(f"{run.run_id}: {got} passes, published {want}")

    missing_runs = sorted(set(PUBLISHED["passes_by_run"]) - {r.run_id for r in runs})
    if missing_runs:
        failures.append(f"published runs not found: {', '.join(missing_runs)}")

    by_id = {r.run_id: r for r in runs}
    if "bare-glm5-r1" in by_id and "bare-glm52-r1" in by_id:
        a, b = by_id["bare-glm5-r1"], by_id["bare-glm52-r1"]
        only_a = sum(1 for c in outcomes if a.rows[c]["passed"] and not b.rows[c]["passed"])
        only_b = sum(1 for c in outcomes if b.rows[c]["passed"] and not a.rows[c]["passed"])
        if only_a != PUBLISHED["r1_only_glm5"]:
            failures.append(f"r1 only-GLM-5 {only_a} != {PUBLISHED['r1_only_glm5']}")
        if only_b != PUBLISHED["r1_only_glm52"]:
            failures.append(f"r1 only-GLM-5.2 {only_b} != {PUBLISHED['r1_only_glm52']}")
        if only_a + only_b != PUBLISHED["r1_discordant"]:
            failures.append(f"r1 discordant {only_a + only_b} != {PUBLISHED['r1_discordant']}")

    for model, want in PUBLISHED["self_flip"].items():
        got = sum(1 for o in outcomes.values() if model in o.by_model and o.self_flips(model))
        if got != want:
            failures.append(f"{model} self-flip {got} != {want}")

    groups = Counter(o.group for o in outcomes.values())
    if sum(groups.values()) != len(outcomes):
        failures.append("group counts do not sum to the case count")
    for name, want in PUBLISHED["groups"].items():
        if groups.get(name, 0) != want:
            failures.append(f"group {name} {groups.get(name, 0)} != {want}")

    return failures


# --------------------------------------------------------------------------------------
# Report
# --------------------------------------------------------------------------------------


def fisher_exact(a: int, b: int, c: int, d: int) -> float:
    """Two-sided Fisher exact p for the 2x2 table [[a, b], [c, d]].

    Written out rather than pulled in: the project has no scipy dependency, and a chi-square
    approximation is not defensible at these cell counts.
    """
    n = a + b + c + d
    if n == 0 or (a + b) == 0 or (c + d) == 0 or (a + c) == 0 or (b + d) == 0:
        return 1.0

    def prob(i: int) -> float:
        j, k = a + b - i, a + c - i
        length = c + d - k
        if j < 0 or k < 0 or length < 0:
            return 0.0
        return math.comb(a + b, i) * math.comb(c + d, k) / math.comb(n, a + c)

    observed = prob(a)
    return min(
        1.0, sum(p for i in range(min(a + b, a + c) + 1) if (p := prob(i)) <= observed + 1e-12)
    )


def sign_test(pos: int, neg: int) -> float:
    """Two-sided exact sign test on the discordant pairs only. Ties carry no information."""
    n = pos + neg
    if n == 0:
        return 1.0
    tail = sum(math.comb(n, k) for k in range(min(pos, neg) + 1))
    return min(1.0, 2 * tail / 2**n)


def enrichment(report_groups: dict[str, int], hits: dict[str, int]) -> dict[str, Any]:
    """Is a signal concentrated in `both_fail`, or does it just describe the corpus?"""
    a = hits.get("both_fail", 0)
    b = report_groups.get("both_fail", 0) - a
    c = hits.get("both_pass", 0)
    d = report_groups.get("both_pass", 0) - c
    return {
        "both_fail": f"{a}/{a + b}",
        "both_pass": f"{c}/{c + d}",
        "p_value": round(fisher_exact(a, b, c, d), 4),
    }


def build_report(
    runs: list[RunRows],
    outcomes: dict[str, CaseOutcome],
    cases: dict[str, Case],
) -> dict[str, Any]:
    models = sorted({r.model for r in runs})
    groups: dict[str, list[str]] = defaultdict(list)
    for cid, outcome in outcomes.items():
        groups[outcome.group].append(cid)

    mechanisms: dict[str, Counter[str]] = {g: Counter() for g in groups}
    case_mechanism: dict[str, Counter[str]] = {}
    deterministic: dict[str, str] = {}
    for cid, outcome in outcomes.items():
        counts: Counter[str] = Counter()
        signatures: set[frozenset[str]] = set()
        for run in runs:
            row = run.rows[cid]
            if row["passed"]:
                continue
            counts[classify(row)] += 1
            signatures.add(failing_tests(row))
        case_mechanism[cid] = counts
        # `grade.detail` keeps only the last 1500 bytes of stdout, so a long enough failure
        # section can push the summary out of it. A signature that cannot be read is reported as
        # unread rather than folded into "varies", which would read as flakiness.
        if not signatures:
            deterministic[cid] = "no_failures"
        elif any(not s for s in signatures):
            deterministic[cid] = "unparsed"
        elif len(signatures) == 1:
            deterministic[cid] = "same_tests"
        else:
            deterministic[cid] = "varies"
        # One vote per case, not per run: six runs of the same case are not six observations of
        # a mechanism. Ties are reported as such rather than broken arbitrarily.
        if counts:
            top = counts.most_common()
            label = top[0][0] if len(top) == 1 or top[0][1] > top[1][1] else "mixed_mechanism"
            mechanisms[outcome.group][label] += 1

    infer = {cid: inferability(case) for cid, case in cases.items()}
    infer_by_group = {g: sum(1 for cid in ids if not infer[cid].clean) for g, ids in groups.items()}
    # The negative control. A heuristic that fires as often on cases both models solve is
    # describing the corpus, not the failures — `§5.9`, where a clean number meant the sample
    # could not tell the two rules apart.
    literals_by_group = {
        g: sum(1 for cid in ids if infer[cid].unknowable_literals) for g, ids in groups.items()
    }
    data_only = {cid: defect_is_data_only(case) for cid, case in cases.items()}
    withheld = {cid: data_defect_is_unspecified(case) for cid, case in cases.items()}
    data_only_by_group = {g: sum(1 for cid in ids if data_only[cid]) for g, ids in groups.items()}
    withheld_by_group = {g: sum(1 for cid in ids if withheld[cid]) for g, ids in groups.items()}
    # Only the cases where the verdict could be reached. Counting `None` as "not data-only"
    # would put the JavaScript cases into the denominator of a Python-only predicate.
    decided = {g: sum(1 for cid in ids if data_only[cid] is not None) for g, ids in groups.items()}
    # A case is accounted for if either check fires: the grader demands an identifier nothing
    # visible names, or the fix is a value the prompt withholds. The residual is what is left
    # for "the models are simply not good enough".
    accounted = {
        g: sorted(cid for cid in ids if not infer[cid].clean or withheld[cid])
        for g, ids in groups.items()
    }
    # Cases from one source file share their context, so their outcomes are correlated and
    # Fisher's independence assumption does not hold across them. `HANDOFF §0.-1` states the
    # rule this repo follows: report the clustering and analyse by cluster rather than drop
    # samples. 45 cases come from 27 source files; `settings.py` alone contributes 4.
    cluster_of = {
        cid: str(case.metadata.get("reverse_source") or cid).split("#")[0]
        for cid, case in cases.items()
    }
    cluster_hits: dict[str, set[str]] = defaultdict(set)
    cluster_total: dict[str, set[str]] = defaultdict(set)
    for group, ids in groups.items():
        for cid in ids:
            cluster_total[group].add(cluster_of[cid])
            if cid in accounted[group]:
                cluster_hits[group].add(cluster_of[cid])

    # Does removing the accounted-for cases restore discrimination? Two removal sets, because
    # one of the three discordant cases is itself accounted for, and dropping it shrinks the
    # numerator of the very metric under test. Both are reported; neither is chosen silently.
    after_removal = {}
    for label, drop in (
        ("drop_both_fail_only", set(accounted["both_fail"])),
        ("drop_all_accounted", {cid for ids in accounted.values() for cid in ids}),
    ):
        kept = [cid for cid in outcomes if cid not in drop]
        discordant = [cid for cid in kept if outcomes[cid].group == "discordant"]
        after_removal[label] = {
            "removed": len(drop),
            "n": len(kept),
            "discordant": f"{len(discordant)}/{len(kept)}",
            "discordance": round(len(discordant) / len(kept), 4) if kept else None,
            "self_flip": {
                m: f"{sum(1 for cid in kept if outcomes[cid].self_flips(m))}/{len(kept)}"
                for m in models
            },
        }

    # The continuous measure the grader already records. If binary pass/fail is only too coarse,
    # this is where the signal would appear; a suite that never collected earns no partial
    # credit, so `None` is 0 rather than dropped.
    def ratio(row: dict[str, Any]) -> float:
        value = row.get("test_pass_ratio")
        return 0.0 if value is None else float(value)

    per_model_ratio = {
        m: {cid: [ratio(r.rows[cid]) for r in runs if r.model == m] for cid in outcomes}
        for m in models
    }
    continuous: dict[str, Any] = {}
    if len(models) == 2:
        first, second = models
        diffs = {
            cid: sum(per_model_ratio[first][cid]) / len(per_model_ratio[first][cid])
            - sum(per_model_ratio[second][cid]) / len(per_model_ratio[second][cid])
            for cid in outcomes
        }
        nonzero = {cid: d for cid, d in diffs.items() if abs(d) > 1e-9}
        pos = sum(1 for d in nonzero.values() if d > 0)
        continuous = {
            "mean_by_model": {
                m: round(
                    sum(sum(v) / len(v) for v in per_model_ratio[m].values()) / len(outcomes), 4
                )
                for m in models
            },
            "mean_difference": f"{first} − {second} = {sum(diffs.values()) / len(diffs):+.4f}",
            "cases_with_a_difference": f"{len(nonzero)}/{len(outcomes)}",
            "direction": f"{pos} favour {first}, {len(nonzero) - pos} favour {second}",
            "sign_test_p": round(sign_test(pos, len(nonzero) - pos), 4),
            # The like-for-like comparison. The binary figure quoted elsewhere collapses each
            # model's three repeats by majority; this one collapses them by mean, as the
            # continuous measure does, so the two are on the same footing.
            "binary_cases_with_a_difference": "{}/{}".format(
                sum(
                    1
                    for cid in outcomes
                    if abs(
                        sum(outcomes[cid].by_model[first]) / len(outcomes[cid].by_model[first])
                        - sum(outcomes[cid].by_model[second]) / len(outcomes[cid].by_model[second])
                    )
                    > 1e-9
                ),
                len(outcomes),
            ),
        }

    failing_rows = [
        run.rows[cid] for run in runs for cid in outcomes if not run.rows[cid]["passed"]
    ]
    residual = [cid for cid in groups.get("both_fail", []) if cid not in accounted["both_fail"]]

    return {
        "models": models,
        "runs": [
            {
                "run_id": r.run_id,
                "model": r.model,
                "passes": sum(bool(x["passed"]) for x in r.rows.values()),
            }
            for r in runs
        ],
        "groups": {g: sorted(ids) for g, ids in groups.items()},
        "group_sizes": {g: len(ids) for g, ids in groups.items()},
        "unanimity": dict(Counter(o.unanimity for o in outcomes.values()).most_common()),
        "mechanisms": {g: dict(c.most_common()) for g, c in mechanisms.items()},
        "failure_signature": {
            g: dict(Counter(deterministic[cid] for cid in ids).most_common())
            for g, ids in groups.items()
        },
        "uninferable": {
            cid: {
                "group": outcomes[cid].group,
                "symbols": infer[cid].unknowable_symbols,
                "kwargs": infer[cid].unknowable_kwargs,
            }
            for cid in sorted(infer)
            if not infer[cid].clean
        },
        "mechanisms_run_level": dict(
            Counter(
                classify(run.rows[cid])
                for run in runs
                for cid in outcomes
                if not run.rows[cid]["passed"]
            ).most_common()
        ),
        "uninferable_by_group": infer_by_group,
        "unknowable_literals_by_group": literals_by_group,
        "data_only_defect_by_group": data_only_by_group,
        "data_only_withheld_by_group": withheld_by_group,
        "data_only_decided_by_group": decided,
        "data_only_defect": sorted(cid for cid, v in data_only.items() if v),
        "data_only_but_specified": sorted(
            cid for cid in data_only if data_only[cid] and not withheld[cid]
        ),
        "accounted_for": accounted,
        "accounted_for_by_group": {g: len(ids) for g, ids in accounted.items()},
        "clusters": {
            "key": "metadata.reverse_source",
            "distinct_source_files": len(set(cluster_of.values())),
            "hits_by_group": {g: len(v) for g, v in cluster_hits.items()},
            "total_by_group": {g: len(v) for g, v in cluster_total.items()},
            "spanning_groups": sorted(
                name
                for name in set(cluster_of.values())
                if sum(1 for g in groups if name in cluster_total[g]) > 1
            ),
        },
        "after_removal": after_removal,
        "continuous_measure": continuous,
        "mean_test_pass_ratio": {
            "all_runs": round(
                sum(ratio(r) for run in runs for r in run.rows.values())
                / (len(runs) * len(outcomes)),
                4,
            ),
            "failing_runs_only": round(sum(ratio(r) for r in failing_rows) / len(failing_rows), 4)
            if failing_rows
            else None,
            "residual_both_fail_by_model": {
                m: round(
                    sum(
                        sum(per_model_ratio[m][cid]) / len(per_model_ratio[m][cid])
                        for cid in residual
                    )
                    / len(residual),
                    4,
                )
                for m in models
            }
            if residual
            else {},
        },
        "enrichment": {
            "uninferable_interface": enrichment(
                {g: len(ids) for g, ids in groups.items()}, infer_by_group
            ),
            "unknowable_assert_literal": enrichment(
                {g: len(ids) for g, ids in groups.items()}, literals_by_group
            ),
            # Denominator is the cases where the predicate applies at all, not every case.
            "data_only_defect": enrichment(decided, data_only_by_group),
            "data_only_and_withheld": enrichment(decided, withheld_by_group),
            # The union. Reported over the Python cases as well as over all of them: the
            # data-only half reaches no verdict on JavaScript, and JavaScript sits 5/15 in
            # `both_pass` against 3/27 in `both_fail`, so padding both denominators with cases
            # one predicate cannot judge moves this p in the direction being claimed. It is not
            # the conservative choice, and an earlier comment here said it was.
            "either_deterministic_defect": enrichment(
                {g: len(ids) for g, ids in groups.items()},
                {g: len(ids) for g, ids in accounted.items()},
            ),
            "either_deterministic_defect_python_only": enrichment(
                decided, {g: len(ids) for g, ids in accounted.items()}
            ),
            # Cluster level: one vote per source file. Fisher assumes independent observations
            # and cases sharing a file do not provide them.
            "either_deterministic_defect_by_cluster": enrichment(
                {g: len(v) for g, v in cluster_total.items()},
                {g: len(v) for g, v in cluster_hits.items()},
            ),
        },
        "unknowable_literals": {
            cid: {"group": outcomes[cid].group, "literals": infer[cid].unknowable_literals}
            for cid in sorted(infer)
            if infer[cid].unknowable_literals
        },
        "language_by_group": {
            g: dict(Counter(cases[cid].language for cid in ids).most_common())
            for g, ids in groups.items()
        },
        "per_case": {
            cid: {
                "group": outcomes[cid].group,
                "language": cases[cid].language,
                "by_model": outcomes[cid].by_model,
                "unanimity": outcomes[cid].unanimity,
                "mechanisms": dict(case_mechanism[cid].most_common()),
                "failure_signature": deterministic[cid],
                "uninferable": not infer[cid].clean,
                "data_only": data_only[cid],
                "data_withheld": withheld[cid],
                "cluster": cluster_of[cid],
            }
            for cid in sorted(outcomes)
        },
    }


def render(report: dict[str, Any]) -> str:
    lines: list[str] = []
    add = lines.append
    add("# 零成本诊断：45 条为什么分不开模型")
    add("")
    add("## 每轮通过数")
    add("")
    add("| run | model | passes / 45 |")
    add("| --- | --- | ---: |")
    for r in report["runs"]:
        add(f"| {r['run_id']} | {r['model']} | {r['passes']} |")
    add("")

    add("## 分组（按各模型三轮多数票）")
    add("")
    add("| 组 | n | 语言 | 接口不可猜 | 数据缺陷（其中题面未给值） | 断言字面量不可见 |")
    add("| --- | ---: | --- | ---: | ---: | ---: |")
    for group in ("both_pass", "discordant", "both_fail"):
        ids = report["groups"].get(group, [])
        if not ids:
            continue
        langs = ", ".join(f"{k} {v}" for k, v in report["language_by_group"][group].items())
        add(
            f"| {group} | {len(ids)} | {langs} | {report['uninferable_by_group'].get(group, 0)} "
            f"| {report['data_only_defect_by_group'].get(group, 0)}"
            f"/{report['data_only_decided_by_group'].get(group, 0)}"
            f"（{report['data_only_withheld_by_group'].get(group, 0)}） "
            f"| {report['unknowable_literals_by_group'].get(group, 0)} |"
        )
    add("")
    fail_ids = report["groups"].get("both_fail", [])
    hits = report["accounted_for_by_group"].get("both_fail", 0)
    add(
        f"两项确定性判据合并解释了 both_fail 的 {hits}/{len(fail_ids)} 条，"
        f"其余 {len(fail_ids) - hits} 条没有可指认的构造缺陷。"
    )
    specified = report["data_only_but_specified"]
    if specified:
        add("")
        add(
            f"另有 {len(specified)} 条缺陷只在数据里、但**题面已经写出了那个值**，因此不计入："
            + "、".join(f"`{c}`" for c in specified)
            + "。它们六轮全挂，是关于模型的证据，不是关于用例的。"
        )
    add("")
    add("## 每个信号的富集程度（Fisher 精确检验，both_fail 对 both_pass）")
    add("")
    add("`both_pass` 一列是阴性对照：两组命中率相当，就说明该信号描述的是语料而不是失败原因。")
    add("**每个算出来的信号都在这里，包括阴性对照不为零的那个** —— 只报对照为零的几项会把")
    add("挑选过程本身藏起来。四个检验未做多重比较校正，Bonferroni 后阈值是 0.0125。")
    add("")
    add("| 信号 | both_fail | both_pass | p |")
    add("| --- | ---: | ---: | ---: |")
    for name, e in report["enrichment"].items():
        add(f"| {name} | {e['both_fail']} | {e['both_pass']} | {e['p_value']} |")
    add("")
    cl = report["clusters"]
    add(
        f"聚簇：45 条来自 **{cl['distinct_source_files']}** 个源文件，同源用例共享上下文、结果相关，"
        f"Fisher 的独立性前提在用例级不成立。`_by_cluster` 一行按源文件各计一票。"
        + (
            f"有 {len(cl['spanning_groups'])} 个源文件横跨多个组，在每组各计一次，因此该行是近似。"
            if cl["spanning_groups"]
            else ""
        )
    )
    add("")

    rm = report.get("after_removal") or {}
    if rm:
        add("## 剔掉可指认缺陷的用例之后")
        add("")
        add("| 剔除口径 | 剔除数 | 剩余 | 跨模型不一致 | 自翻转（噪声底） |")
        add("| --- | ---: | ---: | ---: | --- |")
        for label, r in rm.items():
            flips = " / ".join(f"{m} {v}" for m, v in r["self_flip"].items())
            add(f"| {label} | {r['removed']} | {r['n']} | {r['discordant']} | {flips} |")
        add("")
        add("`drop_all_accounted` 会连带剔掉一条 **discordant** 用例 —— 那是被测指标的分子，")
        add("所以两种口径都报，不静默择一。")
        add("")

    cont = report.get("continuous_measure") or {}
    if cont:
        add("## 连续分能不能救回来（`test_pass_ratio`）")
        add("")
        add(f"- 均值：{cont['mean_by_model']}")
        add(f"- 均值差：{cont['mean_difference']}")
        add(
            f"- 逐条有差异的用例：**{cont['cases_with_a_difference']}**"
            f"（同口径的二值折叠是 {cont['binary_cases_with_a_difference']}）"
        )
        add(f"- 方向：{cont['direction']}，符号检验 p = {cont['sign_test_p']}")
        add("")
    mtpr = report.get("mean_test_pass_ratio") or {}
    if mtpr:
        add(
            f"平均 `test_pass_ratio`：全部 270 次运行 **{mtpr['all_runs']}**，"
            f"只算 175 次失败运行 **{mtpr['failing_runs_only']}**。两者不可混用。"
        )
        if mtpr.get("residual_both_fail_by_model"):
            add(f"剩余 both_fail 用例上按模型分：{mtpr['residual_both_fail_by_model']}")
        add("")
    una = report["unanimity"]
    add(
        f"六轮一致：{una.get('never_passed', 0)} 条一次没过、{una.get('never_failed', 0)} 条次次都过、"
        f"{una.get('split', 0)} 条在某个模型的重复之间翻转。"
    )
    add("")

    run_level = report["mechanisms_run_level"]
    add(f"## 失败机制构成 — 逐次运行（n={sum(run_level.values())} 次失败的 case-run）")
    add("")
    add("| 机制 | n |")
    add("| --- | ---: |")
    for name, n in run_level.items():
        add(f"| {name} | {n} |")
    add("")

    for group in ("both_fail", "discordant"):
        mech = report["mechanisms"].get(group)
        if not mech:
            continue
        total = sum(mech.values())
        add(f"## 失败机制构成 — {group}（n={total}，每条用例一票）")
        add("")
        add("| 机制 | n | 占比 |")
        add("| --- | ---: | ---: |")
        for name, n in mech.items():
            add(f"| {name} | {n} | {n / total:.1%} |")
        add("")

    sig = report["failure_signature"].get("both_fail", {})
    add("## both_fail 六轮挂的是不是同一批测试")
    add("")
    add("| 逐轮失败测试集合 | n |")
    add("| --- | ---: |")
    for name, n in sig.items():
        add(f"| {name} | {n} |")
    add("")
    add(
        "`unparsed` = 某轮的 `grade.detail`（stdout 末 1500 字节）里没有可识别的失败摘要，不是「不稳定」。"
    )
    add("")

    if report["uninferable"]:
        add("## 隐藏测试索要求解者读不到的标识符")
        add("")
        add("| case | 组 | 缺失 |")
        add("| --- | --- | --- |")
        for cid, info in report["uninferable"].items():
            missing = sorted(
                {n for names in info["symbols"].values() for n in names}
                | {k for kws in info["kwargs"].values() for k in kws}
            )
            add(f"| `{cid}` | {info['group']} | {', '.join(f'`{m}`' for m in missing)} |")
        add("")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--run", required=True, type=Path, help="ablation run directory")
    ap.add_argument("--case-set", required=True, help="case set the ablation was run on")
    ap.add_argument(
        "--self-check",
        action="store_true",
        help="re-derive the published figures and exit 1 on any mismatch",
    )
    ap.add_argument("--json", type=Path, help="also write the full report as JSON")
    args = ap.parse_args()

    runs = load_runs(args.run)
    cases = {c.case_id: c for c in load_cases(args.case_set)}
    outcomes = outcomes_by_case(runs, sorted(cases))

    # Before the report, not after: a report printed above a failure banner still reads as
    # authoritative, and the numbers in it would be the ones the self-check just rejected.
    problems = self_check(runs, outcomes) if args.self_check else []
    if problems:
        print("SELF-CHECK FAILED — this script's parsing disagrees with HANDOFF §0.-1:")
        for p in problems:
            print(f"  - {p}")
        print("\nNo report printed: every figure below it would come from the same parsing.")
        return 1

    report = build_report(runs, outcomes, cases)
    print(render(report))

    if args.json:
        args.json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    if args.self_check:
        print()
        print("self-check OK: all six published runs present, per-run passes, r1 discordance and")
        print("direction, both self-flip rates, and the 27/15/3 partition match HANDOFF §0.-1.")
        print("It covers counting and grouping only — not the `grade.detail` parsing, and not")
        print("the two static predicates, which the unit tests pin instead.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
