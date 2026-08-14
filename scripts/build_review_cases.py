#!/usr/bin/env python3
"""Build a patch-review case set from an ablation that already ran.

The repair task on `_clean2026` cannot separate GLM-5 from GLM-5.2: 3 of 45 cases discordant
against a self-flip floor of the same size, and on the 30 cases this script draws from, the
three discordant cases *are* GLM-5's three self-flips. `SESSION §4.2` lists changing the task
type as one of the ways out. This builds the cheapest version of that: same cases, same models,
same repeats — the solver picks the real fix out of four candidates instead of writing one.

The candidates are not synthesised. Every distractor is a patch one of these two models actually
wrote and the grader actually failed, recovered from `runs/<ablation>/*/cases/*/workspace/`. The
label is therefore an execution result, not a judgement.

Three things decide whether such an item measures review or measures an artefact, and all three
are handled here rather than left to the gate:

**Formatting.** A model that rewrites the whole file produces a huge diff against a human commit
that changed four lines, and a reviewer can then pick the odd one out without reading any code —
measured at 77% accuracy on a first version of this set. Every candidate and the pre-state are
run through the same formatter before diffing, which collapses the rewrites that were only
churn. What survives is the semantic change.

**Supply.** Distractors that do not parse are a free elimination, and two candidates that are
the same patch make "exactly one is right" false. Both are filtered.

**Authorship.** The distractors were written by the two models under test. Selection balances
them and records who wrote each, so the readout can ask whether a model's errors track the
authorship of the options rather than their content.

Usage:

    uv run python scripts/build_review_cases.py \\
      --run runs/ablation_20260814_111227 --source-set _clean2026 --out-set _review30
"""

from __future__ import annotations

import argparse
import ast
import difflib
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from aibench.cases import case_set_dir, load_cases
from aibench.models import Case

#: One patch per item, judged on its own.
#:
#: The first version of this builder showed four candidates and asked which one was the fix.
#: `scripts/review_case_gate.py` rejected it: picking the candidate whose size was the outlier
#: among the four scored 12 of 16 against a 25% floor, and picking the text outlier scored 8.
#: Size-matched selection did not help, because the leak is not "gold is bigger" — gold was
#: strictly smallest in 4 of 16 and strictly largest in 6. It is that three of the four options
#: come from the same two models on the same case, so *they* cluster and the human commit sits
#: apart. Every dimension shows it, and no choice of distractors removes it while they share a
#: generator.
#:
#: Showing one patch at a time removes the comparison the leak needs. Measured on the same
#: material, the best absolute size threshold reaches 0.562 balanced accuracy against 0.500,
#: and that is with the threshold fitted on the answers it is being scored against.
#:
#: It also costs less supply: one verified-wrong patch per case instead of three, which is what
#: the corpus actually has once near-duplicates collapse.
VERDICTS = ("YES", "NO")

#: A candidate whose diff runs longer than this is dropped rather than shown. One case in a
#: first version carried three whole-file rewrites and cost 24k prompt tokens on its own —
#: 19% of the entire run — for an item no reviewer could read.
MAX_DIFF_LINES = 150

#: Two candidates this similar are the same patch, and "exactly one of these is the fix" stops
#: being true.
DUPLICATE_RATIO = 0.98

#: Long enough that no candidate's own fenced code can close the block. One source patch in
#: `_clean2026` contains a bare triple backtick.
FENCE = "`````"


def normalise(text: str, language: str) -> str:
    """Run the candidate through the same formatter as every other candidate.

    This is the leakage fix, not a cosmetic step. Half of what separates a model's whole-file
    rewrite from a human's four-line commit is whitespace and quote style, and that half is
    visible in a diff without reading a word of the code.
    """
    if language != "python":
        return text
    try:
        done = subprocess.run(
            ["uv", "run", "ruff", "format", "--stdin-filename", "candidate.py", "-"],
            input=text,
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired):
        return text
    return done.stdout if done.returncode == 0 and done.stdout.strip() else text


def parses(text: str, language: str) -> bool:
    if language == "python":
        try:
            ast.parse(text)
        except (SyntaxError, ValueError):
            return False
        return True
    try:
        done = subprocess.run(
            ["node", "--check", "-"], input=text, capture_output=True, text=True, timeout=60
        )
    except (OSError, subprocess.TimeoutExpired):
        return True  # no checker available: do not silently drop the case
    return done.returncode == 0


def unified(before: str, after: str, path: str) -> str:
    return "".join(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
            n=3,
        )
    )


def changed_lines(diff: str) -> int:
    return sum(
        1 for line in diff.splitlines() if line[:1] in "+-" and line[:3] not in ("+++", "---")
    )


@dataclass
class Candidate:
    text: str
    author: str  # "gold" or the model that wrote it
    diff: str = ""
    size: int = 0


@dataclass
class Built:
    case_id: str
    source_id: str
    patch: Candidate
    answer: str
    prompt: str


def collect(run_dir: Path, cases: dict[str, Case]) -> dict[str, list[Candidate]]:
    """Every distinct patch a failing run left behind, tagged with the model that wrote it."""
    found: dict[str, dict[str, str]] = {cid: {} for cid in cases}
    for sub in sorted(run_dir.iterdir()):
        manifest = sub / "run_manifest.json"
        results = sub / "results.jsonl"
        if not (manifest.is_file() and results.is_file()):
            continue
        model = json.loads(manifest.read_text(encoding="utf-8"))["main_model"]
        for line in results.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            cid = row["case_id"]
            if row.get("passed") or cid not in cases:
                continue
            case = cases[cid]
            impl = next((f for f in case.files if f.role == "impl"), None)
            gold = next((g for g in case.grader.gold_files), None)
            if impl is None or gold is None:
                continue
            written = sub / "cases" / cid / "workspace" / impl.path
            if not written.is_file():
                continue
            try:
                text = written.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            if text in (impl.content, gold.content):
                continue
            # First writer wins, so authorship is stable across reruns of this script.
            found[cid].setdefault(text, model)
    return {
        cid: [Candidate(text=t, author=m) for t, m in variants.items()]
        for cid, variants in found.items()
    }


def pick_distractor(gold: Candidate, pool: list[Candidate]) -> Candidate | None:
    """One verified-wrong patch, preferring the one closest to the gold patch in size.

    Size proximity no longer defends against a within-item comparison — there is none — but it
    keeps the two items a case contributes as alike as they can be, so that a model answering
    from patch length alone gains nothing across the pair either.
    """
    usable = [c for c in pool if 0 < c.size <= MAX_DIFF_LINES]
    usable = [
        c
        for c in usable
        if difflib.SequenceMatcher(None, c.text, gold.text).ratio() <= DUPLICATE_RATIO
    ]
    if not usable:
        return None
    return min(usable, key=lambda c: (abs(c.size - gold.size), c.text))


def build_prompt(case: Case, pre: str, patch: Candidate, path: str) -> str:
    return (
        "下面是一次真实的缺陷修复。先看问题描述与出问题时的文件，再看**一个**候选补丁。\n\n"
        f"## 问题描述\n\n{case.prompt}\n\n"
        f"## 出问题时的 `{path}`\n\n{FENCE}\n{pre.rstrip()}\n{FENCE}\n\n"
        f"## 候选补丁\n\n{FENCE}diff\n{patch.diff.rstrip()}\n{FENCE}\n\n"
        "## 你要做的\n\n"
        "判断这个补丁是不是真正的修复——它能不能让该项目自己的测试全部通过。\n"
        '把 `answer.py` 的内容改成 `VERDICT = "YES"` 或 `VERDICT = "NO"`，例如：\n\n'
        '```\nVERDICT = "NO"\n```\n\n'
        "只写这一行。不要解释，不要复述补丁内容。"
    )


CONFTEST = '''\
"""Read the submitted verdict out of `answer.py` without importing it.

The submission is whatever the model put in its fenced block, which is not always valid
Python. Importing it would turn a readable answer into a collection error, and a collection
error is indistinguishable from a wrong answer in the pass rate — the failure `bare_model`'s
own docstring says the adapter exists to avoid. Measured against 14 plausible reply shapes,
importing survives 4 and reading the text survives 12.
"""

import pathlib
import re

import pytest


@pytest.fixture(scope="session")
def verdict():
    """The single verdict the submission names, or None if it names neither or both."""
    text = pathlib.Path(__file__).with_name("answer.py").read_text(encoding="utf-8")
    named = {m.group(0).upper() for m in re.finditer(r"(?i)(?<![A-Za-z0-9_])(YES|NO)(?![A-Za-z0-9_])", text)}
    return next(iter(named)) if len(named) == 1 else None
'''

VISIBLE_TEST = '''\
def test_answer_names_exactly_one_verdict(verdict):
    """Well-posedness, not correctness: the submission has to say YES or NO, not both."""
    assert verdict in ("YES", "NO")
'''

HIDDEN_TEST = """\
def test_the_verdict_is_right(verdict):
    assert verdict == "{answer}"
"""


def build(case: Case, pool: list[Candidate]) -> list[Built]:
    """Two items per case: the real fix, and one patch the grader failed.

    Paired deliberately. Both items carry the same issue and the same pre-state, so a model
    that answers from the issue text alone cannot be right on both, and the pair is balanced by
    construction — 50% YES over the set, which is what makes plain accuracy readable and what
    stops "always say NO" from scoring above chance.
    """
    impl = next((f for f in case.files if f.role == "impl"), None)
    gold_blob = next(iter(case.grader.gold_files), None)
    if impl is None or gold_blob is None:
        return []
    language = case.language
    pre = normalise(impl.content or "", language)
    gold = Candidate(text=normalise(gold_blob.content or "", language), author="gold")
    gold.diff = unified(pre, gold.text, impl.path)
    gold.size = changed_lines(gold.diff)
    if not gold.size or gold.size > MAX_DIFF_LINES:
        return []

    prepared: list[Candidate] = []
    for candidate in pool:
        if not parses(candidate.text, language):
            continue
        candidate.text = normalise(candidate.text, language)
        candidate.diff = unified(pre, candidate.text, impl.path)
        candidate.size = changed_lines(candidate.diff)
        prepared.append(candidate)

    wrong = pick_distractor(gold, prepared)
    if wrong is None:
        return []

    stem = case.case_id.removeprefix("rev-")
    return [
        Built(
            case_id=f"rvw-{stem}-yes",
            source_id=case.case_id,
            patch=gold,
            answer="YES",
            prompt=build_prompt(case, pre, gold, impl.path),
        ),
        Built(
            case_id=f"rvw-{stem}-no",
            source_id=case.case_id,
            patch=wrong,
            answer="NO",
            prompt=build_prompt(case, pre, wrong, impl.path),
        ),
    ]


def to_case_json(built: Built, source: Case, run_name: str) -> dict[str, Any]:
    return {
        "case_id": built.case_id,
        "schema_version": "0.1",
        "task_type": "pairwise",
        # The graded artefact is `answer.py`, so this is python whatever the reviewed code was.
        # With `javascript` here the grader would not recognise pytest's output, and a malformed
        # submission would report no collection error and no pass ratio at all — the format
        # failures would be invisible.
        "language": "python",
        "prompt": built.prompt,
        "context": {
            "files": [
                {"path": "answer.py", "content": 'VERDICT = "?"\n', "role": "impl"},
                {"path": "conftest.py", "content": CONFTEST, "role": "spec"},
                {"path": "test_answer.py", "content": VISIBLE_TEST, "role": "test"},
            ],
            "notes": f"patch review over {source.case_id}; patch recovered from {run_name}",
        },
        "grader": {
            "mode": "script",
            "command": "python -m pytest -q",
            "gold_files": [{"path": "answer.py", "content": f'VERDICT = "{built.answer}"\n'}],
            "hidden_tests": [
                {"path": "test_answer_spec.py", "content": HIDDEN_TEST.format(answer=built.answer)}
            ],
            # Never `answer.py`: the submission *is* that file, and protecting it grades every
            # correct answer as a reward hack while leaving `infra_error` at zero — a 0% pass
            # rate that an acceptance check on `infra_error` would wave through.
            "protected_paths": ["test_answer.py", "conftest.py"],
        },
        "metadata": {
            "generation": "review",
            "source": "clean2026-review",
            "source_case_id": source.case_id,
            "source_language": source.language,
            "answer": built.answer,
            "patch_author": built.patch.author,
            "patch_diff_lines": built.patch.size,
            "split": "auto",
            "review_status": "needs_review",
        },
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--run", required=True, type=Path)
    ap.add_argument("--source-set", required=True)
    ap.add_argument("--out-set", required=True)
    args = ap.parse_args()

    cases = {c.case_id: c for c in load_cases(args.source_set)}
    pools = collect(args.run, cases)

    out_dir = case_set_dir(args.out_set)
    out_dir.mkdir(parents=True, exist_ok=True)
    for stale in out_dir.glob("*.json"):
        stale.unlink()

    kept, sources, dropped = 0, 0, []
    for cid, case in cases.items():
        items = build(case, pools.get(cid, []))
        if not items:
            dropped.append(cid)
            continue
        sources += 1
        for built in items:
            payload = to_case_json(built, case, args.run.name)
            (out_dir / f"{built.case_id}.json").write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            kept += 1

    print(f"built {kept} review items from {sources} source cases in {out_dir}")
    print(f"dropped {len(dropped)} source cases: no parseable, distinct, in-budget wrong patch")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
