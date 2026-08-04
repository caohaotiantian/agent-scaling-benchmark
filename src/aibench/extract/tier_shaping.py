"""Deterministic transforms that give a generated case the shape its tier requires.

Everything here is pure: no network, no model calls. The LLM produces raw material — a stub,
some tests, maybe a reference solution — and these functions turn that into a case that
satisfies :func:`aibench.tiers.check_tier_invariants`, or report honestly that it cannot.

The most useful transform is :func:`split_tests_for_hiding`: a generator that only ever emits
"stub + one visible test file" still yields a T3 case, because the visible tests can be split
into a smoke subset the agent sees and a hidden remainder it does not.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from aibench import languages
from aibench.models import Case
from aibench.tiers import TIER_ORDER, axes_for_tier, check_tier_invariants, strip_bug_markers

_SPEC_SUFFIXES = (".md", ".rst", ".txt")


VALID_ROLES = ("impl", "test", "distractor", "spec")

# Words generators reach for that mean one of the four roles.
_ROLE_ALIASES = {
    "stub": "impl",
    "source": "impl",
    "implementation": "impl",
    "code": "impl",
    "module": "impl",
    "solution": "impl",
    "tests": "test",
    "unittest": "test",
    "pytest": "test",
    "noise": "distractor",
    "irrelevant": "distractor",
    "decoy": "distractor",
    "doc": "spec",
    "docs": "spec",
    "readme": "spec",
    "specification": "spec",
}


def infer_role(path: str) -> str:
    spec = languages.spec_for_path(path)
    if spec and spec.is_test_path(path):
        return "test"
    if path.lower().endswith(_SPEC_SUFFIXES):
        return "spec"
    return "impl"


def normalize_role(declared: Any, path: str) -> str:
    """Coerce a generator-supplied role to the schema enum, falling back to the path.

    Generators reliably invent labels like ``"stub"``. Rejecting the whole case over one
    unrecognised word throws away material that is otherwise fine, so map what is mappable
    and infer the rest.
    """
    value = str(declared or "").strip().lower()
    if value in VALID_ROLES:
        return value
    return _ROLE_ALIASES.get(value) or infer_role(path)


def annotate_roles(case_dict: dict[str, Any]) -> dict[str, Any]:
    """Normalize ``context.files[].role``, filling in what the generator left out."""
    for f in (case_dict.get("context") or {}).get("files") or []:
        f["role"] = normalize_role(f.get("role"), str(f.get("path") or ""))
    return case_dict


def strip_defect_markers(case_dict: dict[str, Any]) -> list[str]:
    """Remove BUG/FIXME comments from non-test files. Returns the paths that were changed."""
    changed: list[str] = []
    strip_todo = case_dict.get("task_type") == "bugfix"
    for f in (case_dict.get("context") or {}).get("files") or []:
        if f.get("role") == "test":
            continue
        before = f.get("content") or ""
        after = strip_bug_markers(before, strip_todo=strip_todo)
        if after != before:
            f["content"] = after
            changed.append(str(f.get("path")))
    return changed


def _split_test_body(
    content: str, *, language: str | None = None
) -> tuple[str, list[tuple[str, str]]]:
    """Split a pytest file into (prelude, [(test_name, source), ...]).

    A test's decorators and the comments attached above it belong to that test: leaving a
    dangling ``@pytest.mark.parametrize`` behind in the visible file would make it unparseable,
    and a case that cannot even be collected fails every configuration equally.
    """
    test_def = languages.spec_for(language).test_def
    lines = content.splitlines(keepends=True)
    starts: list[tuple[int, str]] = []
    for i, line in enumerate(lines):
        m = test_def.match(line)
        if not m:
            continue
        start = i
        while start > 0:
            prev = lines[start - 1].strip()
            if prev.startswith(("@", "#", "//")):
                start -= 1
            else:
                break
        starts.append((start, m.group(1)))
    if not starts:
        return content, []

    prelude = "".join(lines[: starts[0][0]]).rstrip() + "\n"
    blocks: list[tuple[str, str]] = []
    for i, (start, name) in enumerate(starts):
        end = starts[i + 1][0] if i + 1 < len(starts) else len(lines)
        blocks.append((name, "".join(lines[start:end]).rstrip() + "\n"))
    return prelude, blocks


def split_tests_for_hiding(case_dict: dict[str, Any], *, keep_visible: int = 1) -> int:
    """Move all but ``keep_visible`` test functions out of the workspace into hidden tests.

    The agent keeps a smoke test that shows the task's shape; the rest of the specification is
    only applied at grading time, so passing what it can see is no longer evidence of a fix.
    Returns the number of test functions hidden.
    """
    ctx = case_dict.setdefault("context", {})
    grader = case_dict.setdefault("grader", {})
    existing_hidden = list(grader.get("hidden_tests") or [])
    taken_paths = {str(f.get("path")) for f in ctx.get("files") or []}
    taken_paths.update(str(h.get("path")) for h in existing_hidden)

    hidden_count = 0
    for f in ctx.get("files") or []:
        if f.get("role") != "test":
            continue
        prelude, blocks = _split_test_body(
            f.get("content") or "", language=case_dict.get("language")
        )
        if len(blocks) <= keep_visible:
            continue
        visible, hidden = blocks[:keep_visible], blocks[keep_visible:]
        f["content"] = prelude.rstrip() + "\n\n\n" + "\n\n".join(src for _, src in visible)
        spec = languages.spec_for(case_dict.get("language"))
        name = str(f.get("path")).rsplit("/", 1)[-1]
        hidden_path = spec.hidden_test_name(name)
        n = 2
        while hidden_path in taken_paths:
            hidden_path = spec.hidden_test_name(name, marker=f"spec{n}")
            n += 1
        taken_paths.add(hidden_path)
        existing_hidden.append(
            {
                "path": hidden_path,
                "content": prelude.rstrip() + "\n\n\n" + "\n\n".join(src for _, src in hidden),
            }
        )
        hidden_count += len(hidden)

    if existing_hidden:
        grader["hidden_tests"] = existing_hidden
    return hidden_count


def protect_visible_tests(case_dict: dict[str, Any]) -> list[str]:
    """Declare every visible test file as protected, so neutering one fails the case."""
    paths = [
        str(f.get("path"))
        for f in (case_dict.get("context") or {}).get("files") or []
        if f.get("role") == "test"
    ]
    if paths:
        case_dict.setdefault("grader", {})["protected_paths"] = paths
    return paths


def use_whole_suite_command(case_dict: dict[str, Any]) -> None:
    """Point the grader at the whole workspace so injected hidden tests are collected too."""
    grader = case_dict.setdefault("grader", {})
    if grader.get("mode") == "script" and grader.get("hidden_tests"):
        grader["command"] = languages.spec_for(case_dict.get("language")).default_command


def shape_for_tier(case_dict: dict[str, Any], tier: str) -> dict[str, Any]:
    """Apply the transforms a tier needs. Does not decide whether the result qualifies."""
    annotate_roles(case_dict)
    if tier != "T1":
        strip_defect_markers(case_dict)
    if tier in {"T3", "T4", "T5"}:
        split_tests_for_hiding(case_dict, keep_visible=1)
        protect_visible_tests(case_dict)
        use_whole_suite_command(case_dict)
    return case_dict


def settle_tier(case_dict: dict[str, Any], requested: str) -> tuple[str, list[str]]:
    """Label the case with the highest tier its material actually supports.

    ``requested`` chose the generation brief; it does not decide the label. Each tier is tried
    from T5 down, shaping a fresh copy the way that tier needs, and the first one whose
    invariants hold wins. So a draft asked for at T2 that came back with a reference solution
    and six tests is labelled T3, and one asked for at T5 that came back with two files is
    labelled T3 rather than carrying a claim it cannot meet.

    Mutates ``case_dict`` into the winning shape. Returns ``(tier, notes)``; ``tier`` is empty
    when no tier holds, and ``notes`` then says why each was rejected.
    """
    notes: list[str] = []
    for tier in reversed(TIER_ORDER):
        trial = deepcopy(case_dict)
        shape_for_tier(trial, tier)
        meta = trial.setdefault("metadata", {})
        meta["tier"] = tier
        meta["capability_axes"] = axes_for_tier(tier)
        check = check_tier_invariants(Case.from_dict(trial))
        if not check.ok:
            notes.append(f"{tier}: " + "; ".join(v.code for v in check.violations))
            continue
        meta["tier_facts"] = check.facts
        if tier != requested:
            notes.append(f"settled {requested}->{tier}")
        case_dict.clear()
        case_dict.update(trial)
        return tier, notes

    shape_for_tier(case_dict, requested)
    case_dict.setdefault("metadata", {}).pop("tier", None)
    return "", notes
