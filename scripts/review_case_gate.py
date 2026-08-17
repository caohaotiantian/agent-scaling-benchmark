#!/usr/bin/env python3
"""Can these review items be answered without reading the code? Decide before paying.

`audit-cases` cannot judge a patch-review case. `check_stub_fails` passes because `"?"` is
neither YES nor NO; `check_reference_solution` passes because the gold file *is* the answer.
Both are true by construction for every such case whatever the question says, so the set needs
a gate that can actually fail.

The failure this gate exists for is measured, not hypothetical. The first version of this set
showed four candidates and asked which one was the fix; picking the candidate whose diff size
was the outlier among the four scored **12 of 16 against a 25% floor**, and the text outlier
scored 8. Size-matching the candidates did not help — gold was strictly smallest in 4 of 16 and
strictly largest in 6, so the leak was never "gold is bigger". Three of the four options came
from the same two models on the same case, so *those three* cluster and the human commit sits
apart, in size and in text alike. That gate failure is why the set is now one patch per item.

Every heuristic below is blind: it sees the diff's shape, never its meaning. Each fits its own
threshold **on the answers it is then scored against**, which is deliberately generous — a
feature that cannot beat chance even when fitted in-sample is not leaking. If a blind heuristic
wins anyway, a sighted model's score cannot be read as review ability, and the run is not worth
buying — `HANDOFF §5.5`, a solver that cannot exercise an axis scores on something else.

Usage:

    uv run python scripts/review_case_gate.py --case-set _review30
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import statistics
import sys
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from aibench.cases import load_cases
from aibench.models import Case

#: Balanced by construction — every source case contributes one YES and one NO — so chance for
#: a blind classifier is 0.5 on balanced accuracy.
CHANCE = 0.5
#: How far above chance a fitted-in-sample blind classifier may reach before the item set is
#: judged answerable without reading the code. Permutation p decides; this is the report line.
MAX_BLIND_ACCURACY = 0.65
MAX_PROMPT_TOKENS = 12000
#: Rough for CJK-heavy text; only used to flag an outlier, never to bill anything.
CHARS_PER_TOKEN = 2.6
PERMUTATIONS = 200

_CJK = re.compile(r"[一-鿿]")


def binomial_tail(hits: int, n: int, p: float) -> float:
    return sum(math.comb(n, k) * p**k * (1 - p) ** (n - k) for k in range(hits, n + 1))


def diff_of(case: Case) -> str:
    found = re.search(r"## 候选补丁\n\n`{5}diff\n(.*?)\n`{5}", case.prompt, re.S)
    return found.group(1) if found else ""


def added(diff: str) -> list[str]:
    return [ln[1:] for ln in diff.splitlines() if ln.startswith("+") and not ln.startswith("+++")]


def removed(diff: str) -> list[str]:
    return [ln[1:] for ln in diff.splitlines() if ln.startswith("-") and not ln.startswith("---")]


#: Every feature is computable from what the prompt shows, and none of them reads what the code
#: does. `net_lines` is the one that killed the four-way version.
FEATURES: dict[str, Callable[[str], float]] = {
    "changed_lines": lambda d: len(added(d)) + len(removed(d)),
    "net_lines": lambda d: len(added(d)) - len(removed(d)),
    "added_comment_lines": lambda d: sum(1 for ln in added(d) if ln.lstrip().startswith("#")),
    "added_cjk_chars": lambda d: sum(len(_CJK.findall(ln)) for ln in added(d)),
    "mean_added_line_length": lambda d: statistics.mean([len(ln) for ln in added(d)] or [0]),
    "hunk_count": lambda d: sum(1 for ln in d.splitlines() if ln.startswith("@@")),
}


def _fit(values: list[float], labels: list[bool]) -> tuple[float, bool]:
    """The single split on this feature that best separates the labels."""
    best, chosen = -1.0, (0.0, True)
    for cut in sorted(set(values)):
        for above_is_yes in (True, False):
            predictions = [(v > cut) == above_is_yes for v in values]
            tp = sum(1 for p, y in zip(predictions, labels, strict=True) if p and y)
            fn = sum(1 for p, y in zip(predictions, labels, strict=True) if not p and y)
            tn = sum(1 for p, y in zip(predictions, labels, strict=True) if not p and not y)
            fp = sum(1 for p, y in zip(predictions, labels, strict=True) if p and not y)
            score = 0.5 * (tp / max(1, tp + fn) + tn / max(1, tn + fp))
            if score > best:
                best, chosen = score, (cut, above_is_yes)
    return chosen


def loo_accuracy(values: list[float], labels: list[bool]) -> float:
    """Balanced accuracy with the threshold fitted on every item except the one being scored.

    An in-sample fit is worthless here: on 48 items a single fitted cut reaches ~69% balanced
    accuracy against *shuffled* labels, so a 69% reading says nothing. Holding each item out
    puts chance back at 0.5 and makes an absolute ceiling mean something.
    """
    tp = fp = tn = fn = 0
    for i in range(len(values)):
        rest_values = values[:i] + values[i + 1 :]
        rest_labels = labels[:i] + labels[i + 1 :]
        cut, above_is_yes = _fit(rest_values, rest_labels)
        prediction = (values[i] > cut) == above_is_yes
        if prediction and labels[i]:
            tp += 1
        elif prediction:
            fp += 1
        elif labels[i]:
            fn += 1
        else:
            tn += 1
    return 0.5 * (tp / max(1, tp + fn) + tn / max(1, tn + fp))


def permutation_p(
    values: list[float], labels: list[bool], pairs: list[str], observed: float
) -> float:
    """How often a relabelled set reaches this held-out accuracy.

    The null has to respect the design: the set is one YES and one NO per source case, so the
    only relabelling that keeps it balanced and paired is swapping the two labels within a pair.

    A first version rotated the label list instead. The items sort as adjacent NO/YES pairs, so
    an even rotation reproduced the original labelling and an odd one produced its exact
    complement — and `_fit` searches both directions, so the complement scores identically.
    Every trial matched, p came back 1.0 for every feature, and the gate could not fail. That is
    the shape `SESSION §7.4` records: a criterion that cannot fail is not a criterion.

    Swaps are chosen by hashing the trial index, so the family is reproducible without carrying
    a seed as a hidden input.
    """
    order = sorted(set(pairs))
    hits = 0
    for trial in range(1, PERMUTATIONS + 1):
        flip = {pair: hashlib.sha256(f"{trial}:{pair}".encode()).digest()[0] & 1 for pair in order}
        relabelled = [
            (not label) if flip[pair] else label for label, pair in zip(labels, pairs, strict=True)
        ]
        if loo_accuracy(values, relabelled) >= observed:
            hits += 1
    return (hits + 1) / (PERMUTATIONS + 1)


_CHOICE_BLOCK = re.compile(r"## 补丁 ([AB])\n\n`{5}diff\n(.*?)\n`{5}", re.S)
_ISSUE_BLOCK = re.compile(r"## 问题描述\n\n(.*?)\n\n## 补丁 A", re.S)
_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")


def choice_parts(case: Case) -> tuple[str, str, str]:
    """The issue text and the two candidate diffs, as the model is shown them."""
    issue = _ISSUE_BLOCK.search(case.prompt)
    blocks = dict(_CHOICE_BLOCK.findall(case.prompt))
    return (issue.group(1) if issue else ""), blocks.get("A", ""), blocks.get("B", "")


def _overlap(issue: str, diff: str) -> float:
    """Share of the issue's identifiers that appear in the diff's added lines.

    The blind feature that matters for a human-vs-human set. Both patches are real commits to
    the same file, so the size and style tells are gone — but the issue text names the thing it
    is about, and if that alone picks the right patch then the item measures string matching
    rather than review. `HANDOFF §5.5`.
    """
    wanted = {w.lower() for w in _WORD.findall(issue)}
    if not wanted:
        return 0.0
    body = " ".join(added(diff)).lower()
    return sum(1 for w in wanted if w in body) / len(wanted)


CHOICE_HEURISTICS: dict[str, Callable[[str, str, str], str]] = {
    "larger_diff": lambda i, a, b: (
        "A" if len(added(a)) + len(removed(a)) >= len(added(b)) + len(removed(b)) else "B"
    ),
    "smaller_diff": lambda i, a, b: (
        "A" if len(added(a)) + len(removed(a)) <= len(added(b)) + len(removed(b)) else "B"
    ),
    "more_issue_overlap": lambda i, a, b: "A" if _overlap(i, a) >= _overlap(i, b) else "B",
    "more_added_cjk": lambda i, a, b: (
        "A"
        if sum(len(_CJK.findall(x)) for x in added(a))
        >= sum(len(_CJK.findall(x)) for x in added(b))
        else "B"
    ),
    "more_hunks": lambda i, a, b: (
        "A"
        if sum(1 for x in a.splitlines() if x.startswith("@@"))
        >= sum(1 for x in b.splitlines() if x.startswith("@@"))
        else "B"
    ),
}


def check_choice(cases: list[Case]) -> tuple[list[str], dict[str, Any]]:
    """Two human patches, one issue, forced choice. Chance is 1 in 2."""
    problems: list[str] = []
    report: dict[str, Any] = {"n": len(cases), "mode": "choice"}

    answers = [str(c.metadata.get("answer") or "") for c in cases]
    parts = [choice_parts(c) for c in cases]
    for case, (issue, a, b) in zip(cases, parts, strict=True):
        if not (issue.strip() and a.strip() and b.strip()):
            problems.append(f"{case.case_id}: prompt is missing the issue or a candidate")

    positions = Counter(answers)
    report["answer_positions"] = dict(sorted(positions.items()))
    worst = max(positions.values()) if positions else 0
    if binomial_tail(worst, len(cases), 0.5) < 0.05:
        problems.append(f"answer letters concentrate: {dict(positions)}")

    blind: dict[str, Any] = {}
    for name, fn in CHOICE_HEURISTICS.items():
        hits = sum(
            fn(issue, a, b) == want for (issue, a, b), want in zip(parts, answers, strict=True)
        )
        p = binomial_tail(hits, len(cases), 0.5)
        blind[name] = {
            "hits": f"{hits}/{len(cases)}",
            "rate": round(hits / len(cases), 3),
            "p": round(p, 4),
        }
        if p < 0.05:
            problems.append(
                f"blind heuristic `{name}` scores {hits}/{len(cases)} against a 50% floor "
                f"(p={p:.4f}) — the item is answerable without reading the code"
            )
    report["blind_heuristics"] = blind

    authors = Counter(str(c.metadata.get("distractor_author")) for c in cases)
    report["distractor_authors"] = dict(authors)
    if set(authors) != {"human"}:
        problems.append(f"a distractor is not human-written: {dict(authors)}")

    files = Counter(str(c.metadata.get("source_file")) for c in cases)
    report["source_files"] = len(files)
    report["max_items_from_one_file"] = max(files.values()) if files else 0

    oversized = [
        f"{c.case_id}(~{len(c.prompt) / CHARS_PER_TOKEN:.0f})"
        for c in cases
        if len(c.prompt) / CHARS_PER_TOKEN > MAX_PROMPT_TOKENS
    ]
    report["approx_prompt_tokens"] = {
        "median": round(statistics.median(len(c.prompt) / CHARS_PER_TOKEN for c in cases)),
        "max": round(max(len(c.prompt) / CHARS_PER_TOKEN for c in cases)),
        "total_per_pass": round(sum(len(c.prompt) / CHARS_PER_TOKEN for c in cases)),
    }
    if oversized:
        problems.append(f"prompt over {MAX_PROMPT_TOKENS} tokens: {', '.join(oversized)}")
    for case in cases:
        if case.prompt.count("`````") % 2:
            problems.append(f"{case.case_id}: unbalanced fences in the prompt")

    return problems, report


def check(cases: list[Case]) -> tuple[list[str], dict[str, Any]]:
    problems: list[str] = []
    report: dict[str, Any] = {"n": len(cases)}

    labels = [str(c.metadata.get("answer") or "") == "YES" for c in cases]
    diffs = [diff_of(c) for c in cases]
    for case, diff in zip(cases, diffs, strict=True):
        if not diff.strip():
            problems.append(f"{case.case_id}: prompt carries no candidate diff")

    report["answers"] = dict(Counter("YES" if y else "NO" for y in labels))
    if abs(sum(labels) - len(labels) / 2) > len(labels) * 0.05:
        problems.append(f"item set is not balanced: {report['answers']}")

    # Every source case must contribute exactly one YES and one NO, so a model cannot score by
    # noticing that some issues are over-represented in one class.
    per_source: dict[str, list[bool]] = {}
    for case, label in zip(cases, labels, strict=True):
        per_source.setdefault(str(case.metadata.get("source_case_id")), []).append(label)
    unpaired = [s for s, v in per_source.items() if sorted(v) != [False, True]]
    report["source_cases"] = len(per_source)
    if unpaired:
        problems.append(f"{len(unpaired)} source case(s) do not contribute one YES and one NO")

    pair_ids = [str(c.metadata.get("source_case_id")) for c in cases]
    blind: dict[str, Any] = {}
    for name, fn in FEATURES.items():
        values = [fn(d) for d in diffs]
        accuracy = loo_accuracy(values, labels)
        p = permutation_p(values, labels, pair_ids, accuracy)
        blind[name] = {"balanced_accuracy": round(accuracy, 3), "p": round(p, 4)}
        if p < 0.05:
            problems.append(
                f"blind feature `{name}` separates YES from NO at {accuracy:.0%} balanced "
                f"accuracy (permutation p={p:.4f}) — the item is answerable without reading it"
            )
        elif accuracy > MAX_BLIND_ACCURACY:
            problems.append(
                f"blind feature `{name}` reaches {accuracy:.0%} balanced accuracy; below the "
                f"permutation threshold but above the {MAX_BLIND_ACCURACY:.0%} ceiling"
            )
    report["blind_features"] = blind

    authors = Counter(
        str(c.metadata.get("patch_author")) for c, y in zip(cases, labels, strict=True) if not y
    )
    report["wrong_patch_authors"] = dict(authors)
    if len(authors) < 2:
        problems.append(f"wrong patches come from a single author: {dict(authors)}")
    elif max(authors.values()) / sum(authors.values()) > 0.8:
        problems.append(f"wrong-patch authorship is lopsided: {dict(authors)}")

    oversized = []
    for case in cases:
        approx = len(case.prompt) / CHARS_PER_TOKEN
        if approx > MAX_PROMPT_TOKENS:
            oversized.append(f"{case.case_id}(~{approx:.0f})")
        if case.prompt.count("`````") % 2:
            problems.append(f"{case.case_id}: unbalanced fences in the prompt")
    report["approx_prompt_tokens"] = {
        "median": round(statistics.median(len(c.prompt) / CHARS_PER_TOKEN for c in cases)),
        "max": round(max(len(c.prompt) / CHARS_PER_TOKEN for c in cases)),
        "total_per_pass": round(sum(len(c.prompt) / CHARS_PER_TOKEN for c in cases)),
    }
    if oversized:
        problems.append(f"prompt over {MAX_PROMPT_TOKENS} tokens: {', '.join(oversized)}")

    return problems, report


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--case-set", required=True)
    ap.add_argument("--json", type=Path)
    args = ap.parse_args()

    cases = load_cases(args.case_set)
    if any(c.metadata.get("generation") == "review-choice" for c in cases):
        problems, report = check_choice(cases)
        print(
            f"# review case gate — {args.case_set} ({len(cases)} items, "
            f"{report['source_files']} source files, 二选一，运气底 50%)\n"
        )
        print("| blind heuristic | hits | rate | p vs 50% |")
        print("| --- | ---: | ---: | ---: |")
        for name, row in report["blind_heuristics"].items():
            print(f"| {name} | {row['hits']} | {row['rate']:.0%} | {row['p']} |")
        print()
        print(f"answer positions:     {report['answer_positions']}")
        print(f"candidate authors:    {report['distractor_authors']}")
        print(f"max items one file:   {report['max_items_from_one_file']}")
        print(f"approx prompt tokens: {report['approx_prompt_tokens']}")
        print()
        if args.json:
            args.json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        if problems:
            print(f"GATE FAILED — {len(problems)} problem(s):")
            for problem in problems[:20]:
                print(f"  - {problem}")
            if len(problems) > 20:
                print(f"  ... and {len(problems) - 20} more")
            return 1
        print("GATE OK: no blind heuristic beats chance; both candidates are human-written.")
        return 0

    problems, report = check(cases)

    print(
        f"# review case gate — {args.case_set} ({len(cases)} items, {report['source_cases']} source cases)\n"
    )
    print("| blind feature | balanced accuracy | permutation p |")
    print("| --- | ---: | ---: |")
    for name, row in report["blind_features"].items():
        print(f"| {name} | {row['balanced_accuracy']:.0%} | {row['p']} |")
    print()
    print(f"answers:              {report['answers']}")
    print(f"wrong-patch authors:  {report['wrong_patch_authors']}")
    print(f"approx prompt tokens: {report['approx_prompt_tokens']}")
    print()

    if args.json:
        args.json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    if problems:
        print(f"GATE FAILED — {len(problems)} problem(s):")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print("GATE OK: no blind feature separates the classes; set balanced and paired by source.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
