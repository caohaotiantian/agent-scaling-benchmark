"""The diagnostic that reads an ablation that already ran.

Every number this script produces goes straight into a decision about whether to keep buying
cases from this corpus, and the project's own record (`HANDOFF §5.8`, `§7.2`) is three wrong
conclusions drawn from statistics that looked fine. So the parts that can silently miscount are
pinned here, and each has a companion assertion that must fail if the predicate is widened:

* a keyword argument to a standard-library call is not the implementation's interface;
* a literal in an assertion's *message* specifies nothing;
* a literal the hidden test itself wrote to disk is not a demand on the solver.

`--self-check` covers the counting end by re-deriving the published figures from the real run.
"""

import importlib.util
import sys
from pathlib import Path

import pytest

from aibench.models import Case

_SPEC = importlib.util.spec_from_file_location(
    "discrimination_diagnostic",
    Path(__file__).resolve().parent.parent / "scripts" / "discrimination_diagnostic.py",
)
assert _SPEC is not None and _SPEC.loader is not None
diag = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = diag
_SPEC.loader.exec_module(diag)


def _case(
    hidden_body: str,
    *,
    impl: str = "def discover(root):\n    return []\n",
    prompt: str = "扫描结果不对",
) -> Case:
    return Case.from_dict(
        {
            "case_id": "c1",
            "schema_version": "0.1",
            "task_type": "bugfix",
            "language": "python",
            "prompt": prompt,
            "context": {"files": [{"path": "scanner.py", "content": impl, "role": "impl"}]},
            "grader": {
                "mode": "script",
                "command": "python -m pytest -q",
                "gold_files": [{"path": "scanner.py", "content": impl}],
                "hidden_tests": [{"path": "test_scanner_spec.py", "content": hidden_body}],
            },
            "metadata": {},
        }
    )


def _row(detail: str, **kw) -> dict[str, object]:
    row: dict[str, object] = {
        "passed": False,
        "infra_error": False,
        "collection_error": False,
        "reward_hack": False,
        "grade": {"detail": detail},
    }
    row.update(kw)
    return row


# --------------------------------------------------------------------------------------
# Mechanism classification
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("detail", "expected"),
    [
        (
            "exit=1\nE  TypeError: discover() got an unexpected keyword argument 'extra_skip'",
            "missing_kwarg",
        ),
        (
            "exit=1\nE  AttributeError: 'Namespace' object has no attribute 'version'",
            "missing_attr",
        ),
        ("exit=1\nE  NameError: name 'helper' is not defined", "missing_name"),
        ("exit=1\nE  AssertionError: assert 'JDK' in 'nope'", "assertion"),
        ("exit=1\nE  ValueError: bad input", "other_error"),
        ("", "no_detail"),
    ],
)
def test_mechanism_is_read_off_the_grader_output(detail, expected):
    """`failure_category` cannot do this: 170 of this run's 175 failures share one fallback."""
    assert diag.classify(_row(detail)) == expected


def test_a_workspace_the_solver_left_unparseable_is_not_a_hard_case():
    """`_clean2026` passed `audit-cases` 45/45, so a syntax error at grading time was written
    by the model. Counting it as a capability failure inflates difficulty with instrument noise."""
    detail = "exit=2\nE  SyntaxError: unterminated string literal (detected at line 342)"
    assert diag.classify(_row(detail, collection_error=True)) == "solver_broke_file"


def test_a_collection_error_without_a_syntax_error_stays_separate():
    detail = "exit=2\nE  ModuleNotFoundError: No module named 'yaml'"
    assert diag.classify(_row(detail, collection_error=True)) == "collection_error"


def test_infra_error_outranks_whatever_the_output_says():
    assert diag.classify(_row("exit=1\nAssertionError", infra_error=True)) == "infra_error"


def test_failing_test_ids_are_read_from_pytest_and_from_node():
    """A first version had only the pytest form. Node's runner emits neither `FAILED` nor a TAP
    line in these tails, so all three JavaScript cases came back empty and were labelled "the
    summary was truncated away" — one of their details is 547 bytes against a 1500-byte cap."""
    pytest_tail = "FAILED test_a.py::test_one - AssertionError\nERROR test_b.py\n"
    assert diag.failing_tests(_row(pytest_tail)) == frozenset({"test_a.py::test_one", "test_b.py"})
    node_tail = "✖ strips provider prefix for third-party models (0.112833ms)\n"
    assert diag.failing_tests(_row(node_tail)) == frozenset(
        {"strips provider prefix for third-party models"}
    )


def test_the_visible_surface_never_includes_the_gold():
    """The single most load-bearing line in the file, and the one a reviewer showed could be
    broken with every other test still green. `validity.py` carries the same warning: the gold
    is where the withheld name is guaranteed to appear — that is what makes the case pass its
    solvability gate — and it is exactly what the solver cannot read. Counting it as visible
    made a first version of the upstream gate report the contaminated case as clean."""
    case = _case(
        "import scanner\n\n\ndef test_skip():\n    assert scanner.discover('/tmp', extra_skip='x') == []\n",
        impl="def discover(root):\n    return []\n",
    )
    case.grader.gold_files[0].content = "def discover(root, extra_skip=None):\n    return []\n"
    assert "extra_skip" in (case.grader.gold_files[0].content or "")
    assert "extra_skip" not in diag.visible_surface(case)
    assert not diag.inferability(case).clean


# --------------------------------------------------------------------------------------
# Keyword arguments as interface
# --------------------------------------------------------------------------------------


def test_a_keyword_argument_nothing_visible_names_is_not_inferable():
    """The real case: the hidden test calls `discover_config_files(tmp, extra_skip="custom")`
    and `extra_skip` appears in no shipped file, no visible test and not in the prompt. It is in
    the gold file, which is exactly what the solver cannot read."""
    case = _case(
        "import scanner\n\n\ndef test_skip():\n    assert scanner.discover('/tmp', extra_skip='x') == []\n"
    )
    result = diag.inferability(case)
    assert result.unknowable_kwargs == {"test_scanner_spec.py": ["extra_skip"]}
    assert not result.clean


def test_a_keyword_argument_the_prompt_names_is_inferable():
    case = _case(
        "import scanner\n\n\ndef test_skip():\n    assert scanner.discover('/tmp', extra_skip='x') == []\n",
        prompt="希望能传 extra_skip 指定额外跳过的目录",
    )
    assert diag.inferability(case).clean


def test_a_keyword_argument_to_the_standard_library_is_not_the_implementations_interface():
    """The predicate that must not widen. A fixture helper calling `os.makedirs(p,
    exist_ok=True)` says nothing about the implementation, and counting `exist_ok` would fire on
    most of the corpus — a detector that reports everything reports nothing."""
    case = _case(
        "import os\nimport scanner\n\n\ndef test_skip(tmp_path):\n"
        "    os.makedirs(str(tmp_path / 'a'), exist_ok=True)\n"
        "    assert scanner.discover(str(tmp_path)) == []\n"
    )
    assert diag.hidden_call_keywords(case) == {}


def test_a_from_import_binding_is_followed():
    case = _case(
        "from scanner import discover\n\n\ndef test_skip():\n    assert discover('/tmp', extra_skip='x') == []\n"
    )
    assert diag.inferability(case).unknowable_kwargs == {"test_scanner_spec.py": ["extra_skip"]}


def test_a_module_alias_is_followed():
    case = _case(
        "import scanner as sc\n\n\ndef test_skip():\n    assert sc.discover('/tmp', extra_skip='x') == []\n"
    )
    assert "extra_skip" in diag.inferability(case).unknowable_kwargs["test_scanner_spec.py"]


def test_a_keyword_passed_to_an_object_the_implementation_built_is_still_its_interface():
    """Measured on `rev-a60dac9e85e808bf`: the hidden test does `r = rank.Rank({})` and then
    `r.score(..., iteration_no=5)`. `iteration_no` is in the gold and nowhere else. A first
    version resolved only `module.f(...)` and returned nothing for the whole case."""
    case = _case(
        "import scanner\n\n\ndef test_skip():\n"
        "    s = scanner.Scanner({})\n"
        "    assert s.run('/tmp', iteration_no=5) == []\n"
    )
    assert diag.inferability(case).unknowable_kwargs == {"test_scanner_spec.py": ["iteration_no"]}


def test_a_chained_constructor_call_is_followed():
    case = _case(
        "import scanner\n\n\ndef test_skip():\n"
        "    assert scanner.Scanner({}).run('/tmp', iteration_no=5) == []\n"
    )
    assert "iteration_no" in diag.inferability(case).unknowable_kwargs["test_scanner_spec.py"]


def test_a_plain_return_value_does_not_lend_its_methods_to_the_interface():
    """The guard on the rule above. `result = scanner.discover(...)` then
    `result.get(k, default=1)` is `dict.get`, not the case's interface — only a CapWords callee
    binds an object, because a check that reports the standard library reports everything."""
    case = _case(
        "import scanner\n\n\ndef test_skip():\n"
        "    result = scanner.discover('/tmp')\n"
        "    assert result.get('k', default=1) == 1\n"
    )
    assert diag.hidden_call_keywords(case) == {}


# --------------------------------------------------------------------------------------
# Asserted literals
# --------------------------------------------------------------------------------------


def test_a_literal_the_assertion_demands_and_nothing_visible_shows_is_flagged():
    case = _case(
        "import scanner\n\n\ndef test_hint(capsys):\n    scanner.discover('/tmp')\n"
        "    assert 'JDK' in capsys.readouterr().out\n"
    )
    assert diag.inferability(case).unknowable_literals == {"test_scanner_spec.py": ["JDK"]}


def test_an_assertion_message_specifies_nothing():
    """The predicate that must not widen. `assert x, "Should have a search_articles method"`
    describes the failure; treating the sentence as an expectation made this heuristic fire on
    two thirds of the cases both models solve, which is where its negative control lives."""
    case = _case(
        "import scanner\n\n\ndef test_hint():\n"
        "    assert scanner.discover('/tmp') == [], 'Should have returned an empty list'\n"
    )
    assert diag.hidden_assert_literals(case) == {}


def test_a_literal_the_test_itself_established_is_not_a_demand_on_the_solver():
    """The test writes `key1 = value1` into a fixture tree and asserts it comes back. The solver
    reads that value out of the test setup it can see once the test runs, not out of thin air."""
    case = _case(
        "import scanner\n\n\ndef test_roundtrip(tmp_path):\n"
        "    (tmp_path / 'a.conf').write_text('key1 = value1')\n"
        "    assert 'key1 = value1' in scanner.discover(str(tmp_path))\n"
    )
    assert diag.hidden_assert_literals(case) == {}


# --------------------------------------------------------------------------------------
# Data-only defects
# --------------------------------------------------------------------------------------


def _patched(stub: str, gold: str) -> Case:
    case = _case("import scanner\n\n\ndef test_x():\n    assert scanner\n", impl=stub)
    case.grader.gold_files[0].content = gold
    return case


def test_a_fix_that_only_changes_module_level_data_is_a_value_not_a_behaviour():
    """The real shape: an Odoo `__manifest__.py` whose `data` list is missing a view path. The
    grader can check the exact string; no prompt can convey it without giving the answer away."""
    stub = "MANIFEST = {\n    'data': ['views/a.xml'],\n}\n"
    gold = "MANIFEST = {\n    'data': ['views/a.xml', 'views/b.xml'],\n}\n"
    assert diag.defect_is_data_only(_patched(stub, gold)) is True


def test_a_fix_inside_a_function_body_is_not_data_only():
    """The predicate that must not widen. A one-line change inside a function is behaviour, and
    calling it data would move half the corpus into the unanswerable column."""
    stub = "LIMIT = 5\n\n\ndef check(n):\n    return n < 3\n"
    gold = "LIMIT = 5\n\n\ndef check(n):\n    return n <= 3\n"
    assert diag.defect_is_data_only(_patched(stub, gold)) is False


def test_a_gold_identical_to_the_stub_reaches_no_verdict():
    same = "VALUE = 1\n"
    assert diag.defect_is_data_only(_patched(same, same)) is None


def test_a_non_python_gold_reaches_no_verdict():
    """Reported as unknown rather than as False — a JavaScript case has not been checked, and
    counting it as `touches logic` would put it in the denominator of a Python-only predicate.

    The body is deliberately valid Python (`{"a": 1}` parses as an expression with no function
    spans, and would otherwise be graded `True`), so this pins the suffix guard rather than
    passing by accident on a parse failure."""
    case = _patched('{"a": 1}\n', '{"a": 2}\n')
    case.files[0].path = "data.json"
    case.grader.gold_files[0].path = "data.json"
    assert diag.defect_is_data_only(case) is None


def test_deleting_a_branch_out_of_a_function_is_not_data_only():
    """A pure delete has an empty gold-side line range, so the changed set comes back empty and
    the change disappears. Verified: with an unrelated module-level addition alongside it, the
    first version graded a removed guard clause as a data-only defect."""
    stub = "Y = 1\n\n\ndef f(n):\n    if n < 0:\n        return 0\n    return n\n"
    gold = "Y = 2\n\n\ndef f(n):\n    return n\n"
    assert diag.defect_is_data_only(_patched(stub, gold)) is False


def test_swapping_a_decorator_is_not_data_only():
    """`FunctionDef.lineno` points at the `def`, so a decorator sits outside every span. A
    changed route or a `@property` is behaviour."""
    stub = "@app.route('/old')\ndef view():\n    return 1\n"
    gold = "@app.route('/new')\ndef view():\n    return 1\n"
    assert diag.defect_is_data_only(_patched(stub, gold)) is False


def test_a_data_defect_whose_value_the_prompt_states_is_not_unanswerable():
    """Structural and answerable are two questions. `rev-85d5e687ce9f7d51` writes its numbers
    into the prompt and both models still failed it 6/6 — that is evidence about the models."""
    stub = "PUSH = {'length': 500}\n"
    gold = "PUSH = {'length': 800}\n"
    case = _patched(stub, gold)
    case.prompt = "每条问答内容长度应为 800 字"
    assert diag.defect_is_data_only(case) is True
    assert diag.data_defect_is_unspecified(case) is False


def test_a_data_defect_whose_value_the_prompt_withholds_is_unanswerable():
    stub = "PUSH = {'msg_type': 'text'}\n"
    gold = "PUSH = {'msg_type': 'markdown'}\n"
    case = _patched(stub, gold)
    case.prompt = "推送格式不对，请修正配置"
    assert diag.data_defect_is_unspecified(case) is True


def test_a_defect_that_touches_logic_is_never_unanswerable_on_this_axis():
    stub = "def check(n):\n    return n < 3\n"
    gold = "def check(n):\n    return n <= 3\n"
    assert diag.data_defect_is_unspecified(_patched(stub, gold)) is False


# --------------------------------------------------------------------------------------
# Grouping and statistics
# --------------------------------------------------------------------------------------


def _outcome(glm5, glm52):
    return diag.CaseOutcome("c1", by_model={"GLM-5": glm5, "GLM-5.2": glm52})


def test_the_group_is_each_models_majority_over_its_own_repeats():
    """`HANDOFF §0.-1`'s 27 / 15 / 3 is reproduced only by this collapse. `any of 3` gives
    25/15/5 and `all of 3` gives 30/12/3, so the choice renumbers everything downstream."""
    assert _outcome([True, True, False], [True, False, True]).group == "both_pass"
    assert _outcome([True, True, False], [False, False, True]).group == "discordant"
    assert _outcome([False, True, False], [False, False, True]).group == "both_fail"


def test_unanimity_is_reported_apart_from_the_group():
    """A case both majorities call solved may still have flipped, and that is the reliability
    axis `§0.0c` found shows up before the pass rate does."""
    outcome = _outcome([True, True, False], [True, True, True])
    assert outcome.group == "both_pass"
    assert outcome.unanimity == "split"
    assert outcome.self_flips("GLM-5")
    assert not outcome.self_flips("GLM-5.2")


def test_fisher_exact_matches_a_hand_computable_table():
    # The textbook tea-tasting table: 3/4 against 1/4 in a 4x4 design.
    assert diag.fisher_exact(3, 1, 1, 3) == pytest.approx(0.4857, abs=1e-4)
    assert diag.fisher_exact(4, 0, 0, 4) == pytest.approx(0.0286, abs=1e-4)
    # Asymmetric, and a table with an empty margin, which has no information in it at all.
    assert diag.fisher_exact(8, 2, 1, 5) == pytest.approx(0.0350, abs=1e-4)
    assert diag.fisher_exact(5, 0, 5, 0) == 1.0


def test_sign_test_matches_a_hand_computable_table():
    assert diag.sign_test(10, 7) == pytest.approx(0.6291, abs=1e-4)
    assert diag.sign_test(3, 0) == pytest.approx(0.25, abs=1e-9)
    assert diag.sign_test(0, 0) == 1.0


def test_a_signal_spread_evenly_across_both_groups_is_not_enrichment():
    """The negative control, as a test. A detector firing on half of each group carries no
    information about why the failures failed — `§5.9`, where a clean number only meant the
    sample could not tell the two rules apart.

    The rates differ slightly rather than exactly, because a perfectly balanced table returns
    p = 1.0 under any near-correct implementation and would assert nothing."""
    flat = diag.enrichment({"both_fail": 27, "both_pass": 15}, {"both_fail": 13, "both_pass": 7})
    assert flat["p_value"] > 0.5
    concentrated = diag.enrichment(
        {"both_fail": 27, "both_pass": 15}, {"both_fail": 13, "both_pass": 0}
    )
    assert concentrated["p_value"] < 0.01


def test_duplicate_case_ids_in_a_results_file_are_refused(tmp_path):
    """Counting the same case twice is how `§5.8`'s three wrong numbers were produced."""
    run = tmp_path / "exp"
    run.mkdir()
    (run / "run_manifest.json").write_text('{"run_id": "r", "main_model": "M"}')
    (run / "results.jsonl").write_text(
        '{"case_id": "a", "passed": true}\n{"case_id": "a", "passed": false}\n'
    )
    with pytest.raises(ValueError, match="duplicate case_id"):
        diag.load_runs(tmp_path)


def test_a_run_missing_cases_is_refused(tmp_path):
    run = tmp_path / "exp"
    run.mkdir()
    (run / "run_manifest.json").write_text('{"run_id": "r", "main_model": "M"}')
    (run / "results.jsonl").write_text('{"case_id": "a", "passed": true}\n')
    runs = diag.load_runs(tmp_path)
    with pytest.raises(ValueError, match="missing 1 cases"):
        diag.outcomes_by_case(runs, ["a", "b"])
