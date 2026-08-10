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


class TestTheRewriteMustActuallyFixIt:
    """A rewrite is only worth requesting if its acceptance test checks what triggered it.

    `find_disclosures` has no pattern for a function name, so a rewrite that still named the
    function passed the acceptance test unchallenged — the trigger fired, an LLM call was
    spent, and the prompt came back naming the same function.
    """

    def test_a_rewrite_that_still_names_the_function_is_rejected(self):
        from aibench.extract.generate_case import accept_rewrite

        out = accept_rewrite(
            "total() is off by one",
            "The total helper undercounts by one.",
            forbidden_names=["total"],
        )
        assert out == "total() is off by one"

    def test_a_rewrite_that_drops_the_name_is_accepted(self):
        from aibench.extract.generate_case import accept_rewrite

        out = accept_rewrite(
            "total() is off by one",
            "Summing a basket undercounts by one.",
            forbidden_names=["total"],
        )
        assert out == "Summing a basket undercounts by one."

    def test_without_forbidden_names_the_previous_behaviour_is_unchanged(self):
        from aibench.extract.generate_case import accept_rewrite

        assert accept_rewrite("a", "a clean rewrite") == "a clean rewrite"

    def test_a_rewrite_that_still_discloses_the_mechanism_is_still_rejected(self):
        from aibench.extract.generate_case import accept_rewrite

        assert accept_rewrite("x", "the bug is in the loop", forbidden_names=["total"]) == "x"


class TestAttribution:
    """Which function a change belongs to, for changes that are not inside one.

    Latching the last `def` seen and reading only the new file's line numbers was wrong in four
    ways at once. None of them fires on the current 105 cases, so the headline never moved —
    they would have fired on the next case whose fix touched a constant or a decorator, and
    then named the wrong function, which is the silent-wrong-answer shape.
    """

    def _touched(self, before: str, after: str):
        from aibench.tiers import _functions_touched

        return _functions_touched(before, after)

    def test_a_module_level_change_belongs_to_no_function(self):
        assert (
            self._touched(
                "def loader():\n    return 1\n\nMAX = 3\n",
                "def loader():\n    return 1\n\nMAX = 5\n",
            )
            == set()
        )

    def test_a_class_attribute_change_belongs_to_no_function(self):
        assert (
            self._touched(
                "def setup():\n    pass\n\nclass Account:\n    rate = 0.05\n",
                "def setup():\n    pass\n\nclass Account:\n    rate = 0.10\n",
            )
            == set()
        )

    def test_a_decorator_belongs_to_the_function_it_decorates(self):
        # Not to the function declared above it.
        assert self._touched(
            "def helper():\n    pass\n\n@cache(size=8)\ndef compute():\n    return 1\n",
            "def helper():\n    pass\n\n@cache(size=None)\ndef compute():\n    return 1\n",
        ) == {"compute"}

    def test_a_nested_def_does_not_swallow_its_parent(self):
        assert self._touched(
            "def outer():\n    def inner():\n        return 1\n    return 2\n",
            "def outer():\n    def inner():\n        return 1\n    return 3\n",
        ) == {"outer"}

    def test_a_deleted_function_is_named_not_its_neighbour(self):
        # A pure deletion has j1 == j2, so reading only the new file attributes it to whatever
        # now sits at that position — the function *after* the one that was removed.
        assert self._touched(
            "def alpha(x):\n    return x\n\ndef beta(z):\n    return z\n",
            "def beta(z):\n    return z\n",
        ) == {"alpha"}

    def test_an_unparseable_file_still_gets_a_best_effort_answer(self):
        # Two auto-v0 gold files carry unterminated literals from the earlier redaction
        # corruption; the regex fallback is what keeps them working.
        assert self._touched(
            'def total(x):\n    return x - 1\n    s = "unterminated\n',
            'def total(x):\n    return x\n    s = "unterminated\n',
        ) == {"total"}


class TestDeclarationForms:
    def _name(self, line):
        from aibench.tiers import _FUNCTION_DEF

        m = _FUNCTION_DEF.match(line)
        return (m.group(1) or m.group(2)) if m else None

    def test_javascript_export_forms_are_recognised(self):
        assert self._name("export function computeTax(x) {") == "computeTax"
        assert self._name("export default function computeTax(x) {") == "computeTax"
        assert self._name("export async function fetchAll() {") == "fetchAll"

    def test_an_arrow_function_is_a_function(self):
        assert self._name("const identity = (x) => x") == "identity"

    def test_a_plain_constant_is_not_a_function(self):
        assert self._name("const MAX_RETRIES = 3;") is None
