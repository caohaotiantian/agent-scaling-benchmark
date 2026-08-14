#!/usr/bin/env python3
"""Read the patch-review ablation against a rule fixed before the run.

Written and committed before the run was bought, because this project's record is judgements
made after the numbers arrived: `SESSION §7.4` records acceptance criteria that could not fail,
and `HANDOFF §5.7` records a comparison assembled to support a conclusion already reached.

**The floor is 67%, not 50%.** `scripts/review_case_gate.py` rejected this item set and the run
was bought anyway with the leak recorded: the mean length of an added line separates real fixes
from failed ones at 67% held-out balanced accuracy (permutation p = 0.025), because the real fix
is always a human commit and the failed patches are always model output. A model scoring inside
50–67% has shown nothing that style alone does not explain.

The rule, in advance:

1. **Format first.** If either model's unreadable submissions exceed 5% of its runs, stop. A
   collection error is indistinguishable from a wrong answer in the pass rate, and its rate is a
   property of how talkative a model is, not of how well it reviews.
2. **Above the leak.** Each model's accuracy over its 3 x n item-trials must exceed 0.67 with a
   one-sided binomial p < 0.05. Not 0.5.
3. **Separated.** The paired exact test between the two models over the items, collapsed by
   each model's majority of three, must reach p < 0.05.

All three, or the result is not positive. Everything else printed here is description.

Usage:

    uv run python scripts/review_readout.py --run runs/<ablation dir> --case-set _review30
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from aibench.cases import load_cases

#: What a blind, held-out, single-feature classifier already reaches on this set.
BLIND_CEILING = 0.67
#: Above this share of unreadable submissions, the run measured formatting.
MAX_FORMAT_FAILURE = 0.05
ALPHA = 0.05


def binomial_tail(hits: int, n: int, p: float) -> float:
    return sum(math.comb(n, k) * p**k * (1 - p) ** (n - k) for k in range(hits, n + 1))


def sign_test(pos: int, neg: int) -> float:
    n = pos + neg
    if n == 0:
        return 1.0
    return min(1.0, 2 * sum(math.comb(n, k) for k in range(min(pos, neg) + 1)) / 2**n)


def load(run_dir: Path) -> list[tuple[str, str, dict[str, dict[str, Any]]]]:
    out = []
    for sub in sorted(run_dir.iterdir()):
        manifest, results = sub / "run_manifest.json", sub / "results.jsonl"
        if not (manifest.is_file() and results.is_file()):
            continue
        meta = json.loads(manifest.read_text(encoding="utf-8"))
        rows: dict[str, dict[str, Any]] = {}
        for line in results.read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                rows[row["case_id"]] = row
        out.append((meta["run_id"], meta["main_model"], rows))
    return out


_VERDICT = re.compile(r"(?i)(?<![A-Za-z0-9_])(YES|NO)(?![A-Za-z0-9_])")


def submitted(run_dir: Path, run_id: str, case_id: str) -> str | None:
    """The verdict the model actually submitted, or None if the file names zero or both.

    Read from the workspace rather than inferred from pass/fail, because "wrong answer" and
    "unreadable answer" have to be counted apart and the pass rate merges them.
    """
    for sub in run_dir.iterdir():
        if not (sub / "run_manifest.json").is_file():
            continue
        meta = json.loads((sub / "run_manifest.json").read_text(encoding="utf-8"))
        if meta.get("run_id") != run_id:
            continue
        path = sub / "cases" / case_id / "workspace" / "answer.py"
        if not path.is_file():
            return None
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return None
        named = {m.group(0).upper() for m in _VERDICT.finditer(text)}
        return next(iter(named)) if len(named) == 1 else None
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--run", required=True, type=Path)
    ap.add_argument("--case-set", required=True)
    ap.add_argument("--json", type=Path)
    args = ap.parse_args()

    cases = {c.case_id: c for c in load_cases(args.case_set)}
    truth = {cid: str(c.metadata.get("answer")) for cid, c in cases.items()}
    source = {cid: str(c.metadata.get("source_case_id")) for cid, c in cases.items()}
    runs = load(args.run)
    if not runs:
        print(f"no runs under {args.run}")
        return 1

    models = sorted({m for _, m, _ in runs})
    per_model: dict[str, dict[str, list[bool]]] = {m: defaultdict(list) for m in models}
    answers: dict[str, Counter] = {m: Counter() for m in models}
    unreadable: dict[str, int] = dict.fromkeys(models, 0)
    total: dict[str, int] = dict.fromkeys(models, 0)

    for run_id, model, rows in runs:
        for cid, row in rows.items():
            if cid not in truth:
                continue
            total[model] += 1
            per_model[model][cid].append(bool(row["passed"]))
            said = submitted(args.run, run_id, cid)
            if said is None:
                unreadable[model] += 1
            else:
                answers[model][said] += 1

    report: dict[str, Any] = {
        "models": models,
        "n_items": len(cases),
        "blind_ceiling": BLIND_CEILING,
    }
    print(f"# patch-review readout — {args.case_set} ({len(cases)} items)\n")

    # 1. Format.
    print("## 1. 格式（先看这个：读不出来的回复与答错在通过率上无法区分）\n")
    print("| model | runs | 读不出裁决 | 占比 | YES | NO |")
    print("| --- | ---: | ---: | ---: | ---: | ---: |")
    format_ok = True
    for m in models:
        share = unreadable[m] / max(1, total[m])
        format_ok = format_ok and share <= MAX_FORMAT_FAILURE
        print(
            f"| {m} | {total[m]} | {unreadable[m]} | {share:.1%} | {answers[m]['YES']} | {answers[m]['NO']} |"
        )
    report["format"] = {
        m: {"runs": total[m], "unreadable": unreadable[m], "answers": dict(answers[m])}
        for m in models
    }
    print()

    # 2. Accuracy against the leak ceiling.
    print(f"## 2. 正确率（判据是 > {BLIND_CEILING:.0%}，不是 50%）\n")
    print("| model | item-trial 正确 | 正确率 | p vs 67% | 多数票正确率 |")
    print("| --- | ---: | ---: | ---: | ---: |")
    above = True
    accuracy: dict[str, Any] = {}
    for m in models:
        trials = sum(len(v) for v in per_model[m].values())
        hits = sum(sum(v) for v in per_model[m].values())
        p = binomial_tail(hits, trials, BLIND_CEILING)
        majority = sum(1 for v in per_model[m].values() if sum(v) * 2 > len(v))
        above = above and p < ALPHA
        accuracy[m] = {
            "trials": trials,
            "hits": hits,
            "rate": hits / max(1, trials),
            "p_vs_ceiling": p,
        }
        print(
            f"| {m} | {hits}/{trials} | {hits / max(1, trials):.1%} | {p:.4f} | {majority}/{len(cases)} |"
        )
    report["accuracy"] = accuracy
    print()

    # 3. Separation.
    a, b = models[0], models[1] if len(models) > 1 else models[0]
    only_a = only_b = 0
    for cid in cases:
        va = sum(per_model[a][cid]) * 2 > len(per_model[a][cid])
        vb = sum(per_model[b][cid]) * 2 > len(per_model[b][cid])
        only_a += va and not vb
        only_b += vb and not va
    p_paired = sign_test(only_a, only_b)
    flips = {m: sum(1 for v in per_model[m].values() if 0 < sum(v) < len(v)) for m in models}
    print("## 3. 分离度（配对精确检验；自翻转是本任务自己的噪声底，不是修复口径的）\n")
    print(
        f"- 不一致：{only_a + only_b}/{len(cases)}，方向 {a} {only_a} : {only_b} {b}，p = {p_paired:.4f}"
    )
    print("- 自翻转：" + "，".join(f"{m} {flips[m]}/{len(cases)}" for m in models))
    report["separation"] = {
        "discordant": only_a + only_b,
        "only_" + a: only_a,
        "only_" + b: only_b,
        "p": p_paired,
    }
    report["self_flip"] = flips
    print()

    # Description, not criteria.
    by_truth = {
        m: {
            label: (
                sum(sum(per_model[m][cid]) for cid in cases if truth[cid] == label),
                sum(len(per_model[m][cid]) for cid in cases if truth[cid] == label),
            )
            for label in ("YES", "NO")
        }
        for m in models
    }
    print("## 附：按真值分层（识别「一律答 NO」这类退化策略）\n")
    print("| model | 真值 YES 上正确 | 真值 NO 上正确 |")
    print("| --- | ---: | ---: |")
    for m in models:
        yes, no = by_truth[m]["YES"], by_truth[m]["NO"]
        print(f"| {m} | {yes[0]}/{yes[1]} | {no[0]}/{no[1]} |")
    report["by_truth"] = {m: {k: list(v) for k, v in d.items()} for m, d in by_truth.items()}
    print()
    clusters = len({source[cid] for cid in cases})
    print(
        f"聚簇：{len(cases)} 条题来自 {clusters} 个源用例，每个源用例出一 YES 一 NO，逐题检验未按簇校正。"
    )
    print()

    if args.json:
        args.json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print("## 判定（规则在跑之前就写死了）\n")
    checks = [
        (f"格式失败 <= {MAX_FORMAT_FAILURE:.0%}", format_ok),
        (f"两个模型都显著高于 {BLIND_CEILING:.0%}", above),
        (f"配对检验 p < {ALPHA}", p_paired < ALPHA),
    ]
    for label, ok in checks:
        print(f"  [{'x' if ok else ' '}] {label}")
    verdict = all(ok for _, ok in checks)
    print()
    print(
        "阳性：评审形态在这批材料上分开了两个模型。"
        if verdict
        else "**阴性**：三条判据未全部满足，不能读作评审能力的差异。"
    )
    report["verdict"] = verdict
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
