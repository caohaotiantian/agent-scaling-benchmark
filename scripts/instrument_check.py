#!/usr/bin/env python3
"""Acceptance for the instrument fixes: does the artifact identify its run, and is the
discrimination estimator still measuring the case rather than the outage?

Four assertions, and one number that is printed rather than bounded. The corrected estimator
will drop cases the old one kept — on the most recent calibration, 11 of the 13 keeps came from
cases the panel had not fully measured. How far the keep count falls is information about how
much the old selection rested on an artifact, so the check reports it and does not judge it.
A threshold here would only create pressure to pick one that passes.
"""

from __future__ import annotations

import json
import random
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aibench.calibrate import (
    INCOMPLETE_PANEL,
    aggregate_calibration,
    anchor_fingerprint,
    load_anchor_panel,
    read_result_rows,
)
from aibench.io_util import repo_root
from aibench.provenance import environment, harness_digest
from aibench.stats import item_rest_correlation, point_biserial

REFERENCE_CALIBRATION = "runs/calibration_20260809_231654"


def check_run_identity() -> list[str]:
    """The manifest must name the code that produced it."""
    problems = []
    env = environment()
    if env["code_version"] in {None, "", "aibench@0.1.0 / agent@1.0.0"}:
        problems.append(f"code_version is not a revision: {env['code_version']!r}")
    try:
        actual = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=repo_root(),
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        actual = ""
    if actual and str(env["code_version"]).split("-")[0] != actual:
        problems.append(f"code_version {env['code_version']!r} disagrees with git {actual!r}")
    for field in ("harness_digest", "venv_digest", "python_version"):
        if not env.get(field):
            problems.append(f"{field} missing from the environment stamp")
    print(f"run identity: code_version={env['code_version']} harness={env['harness_digest']}")
    return problems


def check_panel_witnesses_the_harness() -> list[str]:
    """Editing an adapter must move the panel fingerprint."""
    anchors, _ = load_anchor_panel(repo_root() / "configs/runs/anchor-panel.yaml")
    before = anchor_fingerprint(anchors)
    adapter = repo_root() / "src/aibench/agents/openai_compat.py"
    original = adapter.read_bytes()
    try:
        adapter.write_bytes(original + b"\n# provenance probe\n")
        harness_digest.cache_clear()
        during = anchor_fingerprint(anchors)
    finally:
        adapter.write_bytes(original)
        harness_digest.cache_clear()
    after = anchor_fingerprint(anchors)

    problems = []
    if not before.startswith("v2:"):
        problems.append(f"anchor_fingerprint lacks its version prefix: {before}")
    if during == before:
        problems.append("editing an adapter did not move anchor_fingerprint")
    if after != before:
        problems.append("anchor_fingerprint did not return to its original value")
    print(f"panel fingerprint: {before} -> {during} (adapter edited) -> {after}")
    return problems


def check_noise_floor(trials: int = 4000, seed: int = 20260811) -> list[str]:
    """A case carrying no signal must not correlate with ability.

    The uncorrected statistic scores a pure-noise case against a total it is part of, so its
    zero distribution sits near 1/sqrt(k) — 0.18 at k=31, above the 0.15 threshold that exists
    to reject exactly such a case.

    4000 draws, not 400: at 400 the standard error of the mean is 0.018, so a |mean| < 0.03
    bound is a 1.7-sigma test that passes on the seed it was written with. Sweeping seeds, it
    failed on 17 of 60. The extra draws cost a third of a second.
    """
    rng = random.Random(seed)
    problems = []
    for k in (7, 31, 126):
        runs = 9
        naive, corrected = [], []
        for _ in range(trials):
            others = [[rng.random() < 0.5 for _ in range(runs)] for _ in range(k - 1)]
            item = [1.0 if rng.random() < 0.5 else 0.0 for _ in range(runs)]
            totals = [sum(1 for row in others if row[r]) + item[r] for r in range(runs)]
            n = point_biserial(item, totals)
            c = item_rest_correlation(item, totals, [k] * runs)
            if n is not None:
                naive.append(n)
            if c is not None:
                corrected.append(c)
        mn = sum(naive) / len(naive) if naive else 0.0
        mc = sum(corrected) / len(corrected) if corrected else 0.0
        print(f"noise floor k={k:<4} naive mean {mn:+.3f}   corrected mean {mc:+.3f}")
        if abs(mc) >= 0.03:
            problems.append(f"corrected r_pb is biased at k={k}: mean {mc:+.3f}")
        if k == 31 and mn < 0.10:
            problems.append(f"naive r_pb bias at k=31 unexpectedly small ({mn:+.3f})")
    return problems


#: Checks that could not run. Reported separately from failures, and separately from a pass.
_skipped: list[str] = []


def report_keep_collapse() -> list[str]:
    """Re-judge a shipped calibration from its raw rows. Printed, never bounded.

    From the rows, not from `calibration.json`: that file stores a verdict and an r_pb computed
    by the old estimator, so re-judging it would exercise the coverage rule and nothing else —
    and the collapse would be attributed to a correction that never ran.
    """
    directory = repo_root() / REFERENCE_CALIBRATION
    stored = directory / "calibration.json"
    if not stored.is_file():
        print(f"keep collapse: SKIPPED, {REFERENCE_CALIBRATION} not present")
        _skipped.append(f"keep collapse ({REFERENCE_CALIBRATION} not present)")
        return []

    runs = []
    for sub in sorted(directory.glob("*")):
        matched = re.search(r"cal-(.+)-r\d+$", sub.name)
        if not matched:
            continue
        rows = read_result_rows(sub / "results.jsonl")
        if rows:
            runs.append({"anchor": matched.group(1), "rows": rows})
    if not runs:
        print(f"keep collapse: SKIPPED, no result rows under {REFERENCE_CALIBRATION}")
        _skipped.append(f"keep collapse (no result rows under {REFERENCE_CALIBRATION})")
        return []

    before = json.loads(stored.read_text(encoding="utf-8"))
    was = sum(1 for c in before.get("cases") or [] if c.get("keep"))
    after = aggregate_calibration(runs)
    cases = after["cases"]
    now = sum(1 for c in cases if c["keep"])
    incomplete = int(after.get("incomplete_panel_count") or 0)

    # Which of the two changes did the work. The coverage rule and the estimator are separate
    # claims, and the documentation should not credit one for the other's effect.
    old_by_id = {c["case_id"]: c for c in before.get("cases") or []}
    full = [c for c in cases if not any(r.startswith(INCOMPLETE_PANEL) for r in c["reasons"])]
    flipped = sum(
        1 for c in full if bool(c["keep"]) != bool(old_by_id.get(c["case_id"], {}).get("keep"))
    )

    print(
        f"keep collapse on {directory.name}: {was} kept before, {now} after, "
        f"{incomplete} blocked as {INCOMPLETE_PANEL} (of {len(cases)} cases)"
    )
    print(
        f"  attributable to the estimator: {flipped} of the {len(full)} fully-measured cases "
        f"changed verdict; the rest of the drop is the coverage rule."
    )
    print(
        "  cases blocked for coverage are not bad cases — the panel did not finish "
        "measuring them. The remedy is to re-run, not to discard."
    )
    return []


def main() -> int:
    problems: list[str] = []
    _skipped.clear()
    for check in (
        check_run_identity,
        check_panel_witnesses_the_harness,
        check_noise_floor,
        report_keep_collapse,
    ):
        problems.extend(check())
    print()
    for p in problems:
        print(f"  FAIL {p}")
    for s in _skipped:
        print(f"  SKIP {s}")
    if problems:
        print(f"FAIL ({len(problems)} problem(s))")
        return 1
    if _skipped:
        # A check that did not run is not a check that passed. This script printed PASS in a
        # clone while silently skipping the one assertion that needs the (gitignored) reference
        # calibration -- demonstrated by fabricating a contradicting fixture and watching it
        # still print PASS.
        print(f"INCOMPLETE ({len(_skipped)} check(s) skipped; nothing failed)")
        return 2
    print("PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
