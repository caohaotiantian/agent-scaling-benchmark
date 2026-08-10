"""Deterministic tier shaping: role inference, test hiding, and honest tier settling."""

from aibench.cases import load_schema_validator
from aibench.extract.tier_shaping import (
    infer_role,
    protect_visible_tests,
    settle_tier,
    split_tests_for_hiding,
    strip_defect_markers,
)

VISIBLE_TESTS = (
    "import pytest\n"
    "from clamp import clamp\n"
    "\n\n"
    "def test_inside():\n    assert clamp(5, 0, 9) == 5\n"
    "\n\n"
    "def test_below():\n    assert clamp(-1, 0, 9) == 0\n"
    "\n\n"
    "def test_above():\n    assert clamp(99, 0, 9) == 9\n"
    "\n\n"
    "def test_edge():\n    assert clamp(9, 0, 9) == 9\n"
)


def _draft() -> dict:
    return {
        "case_id": "shape-clamp",
        "schema_version": "0.1",
        "task_type": "bugfix",
        "language": "python",
        "prompt": "Callers report clamp() returns out-of-range values. Make the tests pass.",
        "context": {
            "files": [
                {
                    "path": "clamp.py",
                    "content": "def clamp(x, lo, hi):\n    # BUG: bounds ignored\n    return x\n",
                },
                {"path": "test_clamp.py", "content": VISIBLE_TESTS},
            ]
        },
        "grader": {
            "mode": "script",
            "command": "python -m pytest -q test_clamp.py",
            "gold_files": [
                {
                    "path": "clamp.py",
                    "content": "def clamp(x, lo, hi):\n    return max(lo, min(hi, x))\n",
                }
            ],
        },
        "metadata": {},
    }


def test_infer_role():
    assert infer_role("test_clamp.py") == "test"
    assert infer_role("pkg/clamp_test.py") == "test"
    assert infer_role("README.md") == "spec"
    assert infer_role("pkg/clamp.py") == "impl"


def test_strip_defect_markers_leaves_tests_alone():
    d = _draft()
    d["context"]["files"][1]["content"] += "\n# BUG: keep me, I am in a test\n"
    for f in d["context"]["files"]:
        f["role"] = infer_role(f["path"])
    changed = strip_defect_markers(d)
    assert changed == ["clamp.py"]
    assert "BUG" not in d["context"]["files"][0]["content"]
    assert "BUG" in d["context"]["files"][1]["content"]


def test_split_leaves_one_smoke_test_and_hides_the_rest():
    d = _draft()
    for f in d["context"]["files"]:
        f["role"] = infer_role(f["path"])
    hidden = split_tests_for_hiding(d, keep_visible=1)
    assert hidden == 3

    visible = d["context"]["files"][1]["content"]
    assert "def test_inside" in visible
    assert "def test_below" not in visible

    (hidden_file,) = d["grader"]["hidden_tests"]
    assert hidden_file["path"] == "test_clamp_spec.py"
    # The prelude must travel with the hidden half or it cannot import anything.
    assert "from clamp import clamp" in hidden_file["content"]
    assert hidden_file["content"].count("def test_") == 3


def test_split_is_a_noop_when_there_is_nothing_left_to_hide():
    d = _draft()
    d["context"]["files"][1]["content"] = "def test_only():\n    assert True\n"
    for f in d["context"]["files"]:
        f["role"] = infer_role(f["path"])
    assert split_tests_for_hiding(d, keep_visible=1) == 0
    assert "hidden_tests" not in d["grader"]


def test_protect_visible_tests():
    d = _draft()
    for f in d["context"]["files"]:
        f["role"] = infer_role(f["path"])
    assert protect_visible_tests(d) == ["test_clamp.py"]
    assert d["grader"]["protected_paths"] == ["test_clamp.py"]


def test_settle_reaches_t3_and_rewrites_the_command_for_the_whole_suite():
    d = _draft()
    tier, notes = settle_tier(d, "T3")
    assert tier == "T3", notes
    assert d["metadata"]["capability_axes"] == ["A1", "A5", "A6"]
    assert d["grader"]["command"] == "python -m pytest -q"
    assert d["grader"]["protected_paths"] == ["test_clamp.py"]
    assert "BUG" not in d["context"]["files"][0]["content"]
    validator = load_schema_validator()
    assert sorted(validator.iter_errors(d), key=lambda e: list(e.path)) == []


def test_settle_downgrades_rather_than_mislabelling():
    d = _draft()
    tier, notes = settle_tier(d, "T5")
    assert tier == "T3"
    assert any("settled T5->T3" in n for n in notes)
    assert d["metadata"]["tier"] == "T3"
    assert any("T5: " in n and "too_few_distractors" in n for n in notes)


def test_settle_upgrades_when_the_material_supports_more_than_was_asked():
    """The label describes the artifact, not the request."""
    d = _draft()
    tier, notes = settle_tier(d, "T2")
    assert tier == "T3"
    assert any("settled T2->T3" in n for n in notes)


def test_settle_falls_to_t1_when_the_prompt_gives_the_defect_away():
    d = _draft()
    d["prompt"] = "clamp() uses the wrong comparison operator; the bug is in the return line."
    tier, _ = settle_tier(d, "T2")
    assert tier == "T1"
    assert d["metadata"]["tier"] == "T1"


def test_settle_reports_failure_instead_of_labelling_a_broken_case():
    d = _draft()
    d["context"]["files"] = [{"path": f"m{i}.py", "content": "x = 1\n"} for i in range(7)]
    d["grader"].pop("gold_files")
    tier, notes = settle_tier(d, "T2")
    assert tier == ""
    assert "tier" not in d["metadata"]
    assert notes


def test_split_keeps_decorators_with_their_test():
    """A dangling decorator would make the visible file unparseable for every solver."""
    import ast

    d = _draft()
    d["context"]["files"][1]["content"] = (
        "import pytest\n"
        "from clamp import clamp\n"
        "\n\n"
        "def test_a():\n    assert clamp(5, 0, 9) == 5\n"
        "\n\n"
        "# boundaries\n"
        '@pytest.mark.parametrize("v", [-1, 99])\n'
        "def test_b(v):\n    assert 0 <= clamp(v, 0, 9) <= 9\n"
        "\n\n"
        "def test_c():\n    assert clamp(0, 0, 9) == 0\n"
    )
    for f in d["context"]["files"]:
        f["role"] = infer_role(f["path"])
    assert split_tests_for_hiding(d, keep_visible=1) == 2

    visible = d["context"]["files"][1]["content"]
    hidden = d["grader"]["hidden_tests"][0]["content"]
    ast.parse(visible)
    ast.parse(hidden)
    assert "parametrize" not in visible
    assert "# boundaries" in hidden
    assert "parametrize" in hidden


def test_generator_invented_roles_are_mapped_not_rejected():
    """A generator writing role="stub" must not cost us the whole case."""
    from aibench.extract.tier_shaping import annotate_roles, normalize_role

    assert normalize_role("stub", "kv.py") == "impl"
    assert normalize_role("tests", "test_kv.py") == "test"
    assert normalize_role("decoy", "unrelated.py") == "distractor"
    assert normalize_role("readme", "NOTES.md") == "spec"
    # Unmappable words fall back to what the path says, never to an invalid enum value.
    assert normalize_role("banana", "test_kv.py") == "test"
    assert normalize_role(None, "kv.py") == "impl"

    d = _draft()
    d["context"]["files"][0]["role"] = "stub"
    d["context"]["files"][1]["role"] = "unit-tests"
    annotate_roles(d)
    assert [f["role"] for f in d["context"]["files"]] == ["impl", "test"]


def test_settle_survives_an_invented_role_and_stays_schema_valid():
    d = _draft()
    d["context"]["files"][0]["role"] = "stub"
    tier, _ = settle_tier(d, "T3")
    assert tier == "T3"
    validator = load_schema_validator()
    assert sorted(validator.iter_errors(d), key=lambda e: list(e.path)) == []


def test_distractors_are_derived_not_requested():
    """A forced-T4 probe got role=distractor from the generator 0 times in 10, so the label is
    derived from the same evidence the invariant check verifies against."""
    from aibench.extract.tier_shaping import annotate_roles, label_unreferenced_as_distractors

    d = _draft()
    d["context"]["files"].extend(
        [
            {"path": "unrelated.py", "content": "def orphan():\n    return 1\n"},
            {"path": "helper.py", "content": "def helper():\n    return 2\n"},
        ]
    )
    # clamp.py imports helper, so helper is needed even though the fix does not touch it.
    d["context"]["files"][0]["content"] += "from helper import helper\n"
    annotate_roles(d)
    labelled = label_unreferenced_as_distractors(d)

    assert labelled == ["unrelated.py"]
    roles = {f["path"]: f["role"] for f in d["context"]["files"]}
    assert roles["unrelated.py"] == "distractor"
    assert roles["helper.py"] == "impl", "a referenced module is not a distractor"
    assert roles["clamp.py"] == "impl", "the file the solution fixes is never a distractor"
    assert roles["test_clamp.py"] == "test"


def test_a_derived_distractor_survives_the_invariant_check():
    from aibench.models import Case
    from aibench.tiers import check_tier_invariants

    d = _draft()
    d["context"]["files"].append({"path": "orphan.py", "content": "X = 1\n"})
    settle_tier(d, "T4")
    # T4 needs more than this draft can offer, but whatever tier it lands on must be consistent.
    check = check_tier_invariants(Case.from_dict(d))
    assert not any(v.code == "distractor_in_solution" for v in check.violations)
