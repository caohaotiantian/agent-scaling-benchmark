from aibench.cases import load_cases, validate_case_set


def test_seed_case_set_valid():
    assert validate_case_set("seed-v0") == []


def test_load_seed_cases():
    cases = load_cases("seed-v0")
    assert len(cases) >= 3
    ids = {c.case_id for c in cases}
    assert "seed-v0-001-fizzbuzz" in ids
