"""Naming the function the fix changes turns "find the defect" into "read this function".

Measured on the 105 auto-v0 cases carrying a reference solution: the 56 whose prompt names a
changed function average p_hat 0.905, against 0.704 for the 49 that do not — a 20pp gap, and
the strongest prompt-side factor found. It is a warning rather than a rejection because it
fires on half the set; the generator responds by rewriting the prompt.
"""

from aibench.models import Case
from aibench.tiers import check_tier_invariants, prompt_names_changed_function

_STUB = "def helper(x):\n    return x\n\n\ndef total(items):\n    return sum(items) - 1\n"
_FIXED = "def helper(x):\n    return x\n\n\ndef total(items):\n    return sum(items)\n"


def _case(prompt: str, *, gold: str = _FIXED, stub: str = _STUB, **over) -> Case:
    base = {
        "case_id": "names-test",
        "schema_version": "0.1",
        "task_type": "bugfix",
        "language": "python",
        "prompt": prompt,
        "context": {
            "files": [
                {"path": "impl.py", "content": stub},
                {
                    "path": "test_impl.py",
                    "content": "from impl import total\n\ndef test_t():\n    assert total([1]) == 1\n",
                },
            ]
        },
        "grader": {
            "mode": "script",
            "command": "python -m pytest -q",
            "gold_files": [{"path": "impl.py", "content": gold}],
        },
        "metadata": {"tier": "T2"},
    }
    base.update(over)
    return Case.from_dict(base)


class TestDetection:
    def test_the_changed_function_named_in_the_prompt_is_reported(self):
        assert prompt_names_changed_function(_case("total() returns one less than it should")) == [
            "total"
        ]

    def test_a_symptom_only_prompt_reports_nothing(self):
        assert prompt_names_changed_function(_case("Summing a basket undercounts by one.")) == []

    def test_naming_an_unchanged_function_is_not_a_disclosure(self):
        # `helper` is identical in stub and gold, so naming it points at nothing.
        assert prompt_names_changed_function(_case("helper is used throughout")) == []

    def test_a_case_with_no_reference_solution_reports_nothing(self):
        case = _case("total is wrong", grader={"mode": "script", "command": "python -m pytest -q"})
        assert prompt_names_changed_function(case) == []

    def test_short_names_are_ignored(self):
        # A two- or three-letter name collides with ordinary prose.
        stub = "def go(x):\n    return x - 1\n"
        gold = "def go(x):\n    return x\n"
        assert (
            prompt_names_changed_function(_case("go through the list", stub=stub, gold=gold)) == []
        )

    def test_the_dict_form_agrees_with_the_case_form(self):
        case = _case("total() is off by one")
        raw = {
            "prompt": case.prompt,
            "context": {"files": [{"path": f.path, "content": f.content} for f in case.files]},
            "grader": {
                "gold_files": [
                    {"path": g.path, "content": g.content} for g in case.grader.gold_files
                ]
            },
        }
        assert prompt_names_changed_function(raw) == prompt_names_changed_function(case)


class TestTierReporting:
    def test_it_is_a_warning_not_an_error(self):
        # It fires on roughly half the measured set; rejecting would halve the case count.
        check = check_tier_invariants(_case("total() returns one less than it should"))
        hits = [v for v in check.violations if v.code == "prompt_names_changed_function"]
        assert len(hits) == 1
        assert hits[0].severity == "warn"

    def test_a_symptom_only_prompt_raises_nothing(self):
        check = check_tier_invariants(_case("Summing a basket undercounts by one."))
        assert not [v for v in check.violations if v.code == "prompt_names_changed_function"]

    def test_t1_is_allowed_to_name_it(self):
        # T1 is the floor anchor: it may point straight at the defect.
        case = _case("total() is wrong", metadata={"tier": "T1"})
        check = check_tier_invariants(case)
        assert not [v for v in check.violations if v.code == "prompt_names_changed_function"]
