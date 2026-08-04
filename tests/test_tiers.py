"""Tier structural invariants and the defect-disclosure detector."""

import pytest

from aibench.models import Case
from aibench.tiers import (
    TIER_ORDER,
    check_tier_invariants,
    count_test_functions,
    find_bug_markers,
    find_disclosures,
    strip_bug_markers,
    tier_spec,
)

HIDDEN = (
    "from clamp import clamp\n\n\n"
    "def test_below():\n    assert clamp(-1, 0, 9) == 0\n\n\n"
    "def test_above():\n    assert clamp(99, 0, 9) == 9\n\n\n"
    "def test_edge():\n    assert clamp(9, 0, 9) == 9\n\n\n"
    "def test_zero():\n    assert clamp(0, 0, 9) == 0\n"
)


def _case(tier: str, **overrides) -> Case:
    base = {
        "case_id": f"tier-{tier}",
        "schema_version": "0.1",
        "task_type": "bugfix",
        "language": "python",
        "prompt": "Callers report clamp() lets out-of-range values through. Make the tests pass.",
        "context": {
            "files": [
                {"path": "clamp.py", "content": "def clamp(x, lo, hi):\n    return x\n"},
                {"path": "test_clamp.py", "content": "def test_a():\n    pass\n", "role": "test"},
            ]
        },
        "grader": {"mode": "script", "command": "python -m pytest -q"},
        "metadata": {"tier": tier},
    }
    base.update(overrides)
    return Case.from_dict(base)


def _t3_case(**overrides) -> Case:
    files = [
        {"path": "clamp.py", "content": "def clamp(x, lo, hi):\n    return x\n"},
        {"path": "test_clamp.py", "content": "def test_a():\n    pass\n", "role": "test"},
    ]
    grader = {
        "mode": "script",
        "command": "python -m pytest -q",
        "hidden_tests": [{"path": "test_hidden.py", "content": HIDDEN}],
        "protected_paths": ["test_clamp.py"],
        "gold_files": [
            {
                "path": "clamp.py",
                "content": "def clamp(x, lo, hi):\n    return max(lo, min(hi, x))\n",
            }
        ],
    }
    return _case("T3", context={"files": files}, grader=grader, **overrides)


def test_every_tier_has_a_spec():
    for t in TIER_ORDER:
        assert tier_spec(t).tier == t
    with pytest.raises(ValueError):
        tier_spec("T9")


def test_t1_tolerates_a_disclosing_prompt():
    c = _case("T1", prompt="The comparison is inverted on line 12; flip it.")
    assert check_tier_invariants(c).ok is True


def test_t2_rejects_a_disclosing_prompt():
    c = _case("T2", prompt="The improvement comparison is inverted — flip the operator.")
    check = check_tier_invariants(c)
    assert check.ok is False
    assert any(v.code == "prompt_discloses_defect" for v in check.violations)


def test_t2_rejects_a_bug_marker_in_the_stub():
    c = _case(
        "T2",
        context={
            "files": [
                {
                    "path": "clamp.py",
                    "content": "def clamp(x, lo, hi):\n    # BUG: bounds ignored\n    return x\n",
                },
                {"path": "test_clamp.py", "content": "def test_a():\n    pass\n", "role": "test"},
            ]
        },
    )
    check = check_tier_invariants(c)
    assert check.ok is False
    assert any(v.code == "stub_has_bug_marker" for v in check.violations)


def test_t2_accepts_a_symptom_only_case():
    assert check_tier_invariants(_case("T2")).ok is True


def test_t3_requires_hidden_tests_reference_solution_and_protection():
    bare = _case("T3")
    codes = {v.code for v in check_tier_invariants(bare).violations}
    assert {"too_few_hidden_tests", "too_few_solution_files", "protected_paths_required"} <= codes
    assert check_tier_invariants(_t3_case()).ok is True


def test_t4_requires_distractors_and_a_multi_file_solution():
    c = _t3_case()
    check = check_tier_invariants(Case.from_dict({**c.raw, "metadata": {"tier": "T4"}}))
    codes = {v.code for v in check.violations}
    assert "too_few_files" in codes
    assert "too_few_distractors" in codes
    assert "too_few_solution_files" in codes


def test_hidden_test_may_not_shadow_a_context_file():
    c = _t3_case()
    raw = {**c.raw}
    raw["grader"] = {**raw["grader"], "hidden_tests": [{"path": "clamp.py", "content": HIDDEN}]}
    check = check_tier_invariants(Case.from_dict(raw))
    assert any(v.code == "hidden_test_shadows_context" for v in check.violations)


def test_protected_path_must_exist_in_context():
    c = _t3_case()
    raw = {**c.raw}
    raw["grader"] = {**raw["grader"], "protected_paths": ["ghost.py"]}
    check = check_tier_invariants(Case.from_dict(raw))
    assert any(v.code == "protected_path_not_in_context" for v in check.violations)


def test_hidden_tests_require_a_script_grader():
    c = _t3_case()
    raw = {**c.raw}
    raw["grader"] = {**raw["grader"], "mode": "gold", "match": "normalized"}
    check = check_tier_invariants(Case.from_dict(raw))
    assert any(v.code == "hidden_tests_need_script_grader" for v in check.violations)


def test_missing_tier_is_a_violation():
    c = _case("T2")
    check = check_tier_invariants(Case.from_dict({**c.raw, "metadata": {}}))
    assert check.ok is False
    assert check.violations[0].code == "tier_missing"


def test_disclosure_detector_separates_mechanism_from_symptom():
    assert find_disclosures("the attention mask uses the wrong diagonal offset")
    assert find_disclosures("it uses value equality instead of identity comparison")
    assert find_disclosures("fix the off-by-one so numbering starts at 1")
    assert find_disclosures("当前实现仍然使用 /api/v4")
    assert find_disclosures("问题出在 resolve() 的递归分支")
    assert not find_disclosures("Users report that once one IP hits the limit, all IPs are blocked")
    assert not find_disclosures("The module cannot even be imported. Find and fix the issue.")


def test_bug_marker_detection_and_stripping():
    src = "def f():\n    # BUG: wrong branch\n    return 1  # FIXME: swap\n"
    assert find_bug_markers(src) is True
    cleaned = strip_bug_markers(src)
    assert "BUG" not in cleaned and "FIXME" not in cleaned
    assert "return 1" in cleaned
    assert find_bug_markers(cleaned) is False


def test_todo_is_a_marker_only_for_bugfix_cases():
    src = "def f():\n    # TODO: implement\n    pass\n"
    assert find_bug_markers(src) is False
    assert find_bug_markers(src, flag_todo=True) is True


def test_count_test_functions():
    assert count_test_functions(HIDDEN) == 4
    assert count_test_functions("def helper():\n    pass\n") == 0


def test_chinese_specification_is_not_treated_as_disclosure():
    """Every task description states expected behaviour; only claims about the current
    implementation give the defect away."""
    assert not find_disclosures("clamp 应该返回 lo 和 hi 之间的值，超出范围的输入应该被截断")
    assert not find_disclosures("用户反馈：某个 IP 触发限流后，所有 IP 都被一起挡住了")
    assert find_disclosures("当前实现用了值相等而不是身份比较")
    assert find_disclosures("插入和删除两列写反了")
    assert find_disclosures("把 /api/v4 改成 /api/v5")
