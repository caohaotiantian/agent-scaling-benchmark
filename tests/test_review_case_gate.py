"""The gate that decides whether a patch-review run is worth buying.

It exists because `audit-cases` cannot judge these cases — `check_stub_fails` and
`check_reference_solution` are true by construction for every one of them — and it has already
rejected two versions of the item set. So the thing that must be pinned is that it can still
say no: a gate that always passes would have waved through an item set answerable at 75%
without reading any code.

The first version could not say no. Its permutation null rotated the label list, and the items
sort as adjacent `-no`/`-yes` pairs, so an even rotation reproduced the labels and an odd one
produced their complement — which `_fit` scores identically because it searches both
directions. Every trial matched the observation and p came back 1.0 for all six features. The
regression test for that is `test_a_planted_leak_is_detected`.
"""

import importlib.util
import sys
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "review_case_gate",
    Path(__file__).resolve().parent.parent / "scripts" / "review_case_gate.py",
)
assert _SPEC is not None and _SPEC.loader is not None
gate = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = gate
_SPEC.loader.exec_module(gate)


#: Twelve source cases, each contributing one YES and one NO, in the order the loader yields.
PAIRS = [f"src-{i:02d}" for i in range(12) for _ in range(2)]
LABELS = [i % 2 == 1 for i in range(24)]


def test_leave_one_out_puts_chance_back_at_a_half():
    """An in-sample fit reaches ~69% on shuffled labels, so only the held-out number is
    readable. A feature carrying nothing must land near 0.5, not near 0.7."""
    flat = [1.0] * len(LABELS)
    assert gate.loo_accuracy(flat, LABELS) == pytest.approx(0.5, abs=1e-9)


def test_a_separable_feature_is_recovered():
    values = [10.0 if y else 1.0 for y in LABELS]
    assert gate.loo_accuracy(values, LABELS) == pytest.approx(1.0)


def test_a_planted_leak_is_detected():
    """The regression for the null that could not reject. A feature that names the label must
    come out significant; under the rotation null it came out at p = 1.0."""
    values = [10.0 if y else 1.0 for y in LABELS]
    observed = gate.loo_accuracy(values, LABELS)
    assert gate.permutation_p(values, LABELS, PAIRS, observed) < 0.05


def test_a_feature_that_carries_nothing_is_not_flagged():
    """The other half: the null must also accept. A gate that rejects everything is as useless
    as one that accepts everything, and it would block a clean set from ever being bought."""
    values = [float(i % 3) for i in range(len(LABELS))]
    observed = gate.loo_accuracy(values, LABELS)
    assert gate.permutation_p(values, LABELS, PAIRS, observed) > 0.05


def test_the_null_respects_the_pairing():
    """Relabelling swaps within a source case, so every trial keeps one YES and one NO per pair
    and the set stays balanced. A null that broke the balance would compare the observation
    against sets the design can never produce."""
    seen = []

    def record(values, labels):
        seen.append(list(labels))
        return 0.0  # never reaches the observation, so every trial is counted as a miss

    original = gate.loo_accuracy
    gate.loo_accuracy = record
    try:
        gate.permutation_p([0.0] * len(LABELS), LABELS, PAIRS, 1.0)
    finally:
        gate.loo_accuracy = original

    assert seen, "the null generated no trials"
    for labels in seen:
        assert sum(labels) == len(labels) // 2
        for i in range(0, len(labels), 2):
            assert labels[i] != labels[i + 1]
    assert any(labels != LABELS for labels in seen), "every trial reproduced the original labels"


def test_the_diff_is_recovered_from_the_prompt():
    """The blind features read the diff out of the rendered prompt rather than the metadata, so
    they see exactly what the model sees. A silent miss here would score every feature on an
    empty string and report a clean set."""

    class Stub:
        prompt = "## 候选补丁\n\n`````diff\n@@ -1,2 +1,2 @@\n-a = 1\n+a = 2\n`````\n\n## 你要做的\n"

    diff = gate.diff_of(Stub())
    assert "+a = 2" in diff
    assert gate.FEATURES["changed_lines"](diff) == 2
    assert gate.FEATURES["hunk_count"](diff) == 1
