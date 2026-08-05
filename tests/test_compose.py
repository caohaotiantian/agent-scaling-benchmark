"""Composing retrieval cases from verified ones."""

from aibench.compose import DISTRACTOR_DIR, compose_case, compose_case_set, donor_files
from aibench.extract.tier_shaping import settle_tier
from aibench.models import Case
from aibench.tiers import check_tier_invariants
from aibench.validity import check_reference_solution, check_stub_fails

HIDDEN = (
    "from clamp import clamp\n\n\n"
    "def test_below():\n    assert clamp(-1, 0, 9) == 0\n\n\n"
    "def test_above():\n    assert clamp(99, 0, 9) == 9\n\n\n"
    "def test_edge():\n    assert clamp(9, 0, 9) == 9\n"
)


def _verified_case(cid: str, module: str) -> dict:
    return {
        "case_id": cid,
        "schema_version": "0.1",
        "task_type": "bugfix",
        "language": "python",
        "prompt": f"Callers report {module}() lets out-of-range values through.",
        "context": {
            "files": [
                {
                    "path": "clamp.py",
                    "content": "def clamp(x, lo, hi):\n    return x\n",
                    "role": "impl",
                },
                {
                    "path": f"{module}_util.py",
                    "content": f"def {module}_helper():\n    return 1\n",
                    "role": "impl",
                },
                {
                    "path": "test_clamp.py",
                    "content": "from clamp import clamp\n\n\n"
                    "def test_inside():\n    assert clamp(5, 0, 9) == 5\n" + HIDDEN,
                    "role": "test",
                },
            ]
        },
        "grader": {
            "mode": "script",
            "command": "python -m pytest -q",
            "gold_files": [
                {
                    "path": "clamp.py",
                    "content": "def clamp(x, lo, hi):\n    return max(lo, min(hi, x))\n",
                }
            ],
        },
        "metadata": {"validity_ok": True},
    }


def test_donor_files_exclude_tests_but_include_the_donors_own_stub():
    """ "Part of the solution" is relative to the host, not the donor. Excluding a donor's own
    stub left 109 of 126 real cases with nothing to donate, because a typical case has one
    implementation file and that file is exactly what its own solution fixes."""
    case = _verified_case("host", "alpha")
    paths = [f["path"] for f in donor_files(case)]
    assert "alpha_util.py" in paths
    assert "clamp.py" in paths
    assert "test_clamp.py" not in paths, "a donated test would be collected and run"


def test_a_donated_stub_cannot_join_the_hosts_solution():
    """The safety property that replaces the exclusion: donors land somewhere unreachable."""
    host = _verified_case("host", "alpha")
    out = compose_case(host, [_verified_case("donor", "beta")], target_files=6)
    settle_tier(out, "T4")

    host_solution = {g["path"] for g in out["grader"]["gold_files"]}
    donated = [f for f in out["context"]["files"] if f["path"].startswith(f"{DISTRACTOR_DIR}/")]
    assert donated, "nothing was planted"
    for f in donated:
        assert f["role"] == "distractor"
        assert f["path"] not in host_solution

    # settle_tier additionally labels the host's own unreferenced files as distractors, which
    # is correct; what must never happen is a distractor the host's fix has to touch.
    for f in out["context"]["files"]:
        if f["role"] == "distractor":
            assert f["path"] not in host_solution
    assert check_tier_invariants(Case.from_dict(out)).ok is True


def test_composition_plants_donors_out_of_the_import_path():
    host = _verified_case("host", "alpha")
    donor = _verified_case("donor", "beta")
    out = compose_case(host, [donor], target_files=6)

    added = [f for f in out["context"]["files"] if f["role"] == "distractor"]
    assert added, "nothing was planted"
    for f in added:
        assert f["path"].startswith(f"{DISTRACTOR_DIR}/")
        assert "/" in f["path"], "a top-level file could shadow the host's own imports"
    assert out["case_id"] == "host-retrieval"
    assert out["metadata"]["composed_from"] == ["donor"]


def test_the_host_case_is_left_untouched():
    """Both validity gates must survive by construction, not by re-verification luck."""
    host = _verified_case("host", "alpha")
    out = compose_case(host, [_verified_case("donor", "beta")], target_files=6)

    original = {f["path"]: f["content"] for f in host["context"]["files"]}
    kept = {f["path"]: f["content"] for f in out["context"]["files"] if f["role"] != "distractor"}
    assert kept == original
    assert out["grader"] == host["grader"]


def test_a_composed_case_still_fails_on_the_stub_and_passes_on_the_solution():
    host = _verified_case("host", "alpha")
    donors = [_verified_case(f"d{i}", m) for i, m in enumerate(["beta", "gamma", "delta"])]
    out = compose_case(host, donors, target_files=6)
    settle_tier(out, "T4")
    case = Case.from_dict(out)

    ok, detail = check_stub_fails(case)
    assert ok is True, detail
    ok, detail = check_reference_solution(case)
    assert ok is True, detail


def test_composition_reaches_t4():
    host = _verified_case("host", "alpha")
    donors = [_verified_case(f"d{i}", m) for i, m in enumerate(["beta", "gamma", "delta"])]
    out = compose_case(host, donors, target_files=6)
    tier, notes = settle_tier(out, "T4")
    assert tier == "T4", notes
    check = check_tier_invariants(Case.from_dict(out))
    assert check.ok is True
    assert check.facts["distractor_count"] >= 1
    assert check.facts["file_count"] >= 5


def test_composing_a_set_rotates_donors_deterministically():
    cases = [_verified_case(f"c{i}", f"m{i}") for i in range(4)]
    first = compose_case_set(cases, target_files=5, donors_per_case=2)
    second = compose_case_set(cases, target_files=5, donors_per_case=2)
    assert [c["case_id"] for c in first] == [c["case_id"] for c in second]
    assert [c["metadata"]["composed_from"] for c in first] == [
        c["metadata"]["composed_from"] for c in second
    ], "a set that changes between runs cannot be compared to an earlier calibration"
    assert all(c["case_id"] not in c["metadata"]["composed_from"] for c in first)


def test_a_single_case_cannot_be_composed():
    assert compose_case_set([_verified_case("only", "alpha")]) == []


def test_donors_can_come_from_a_wider_pool_than_the_hosts():
    """Selection is what makes the curated set small, so drawing donors from it starves
    composition: hosts should be what calibration kept, donors only need to be plausible."""
    hosts = [_verified_case("kept", "alpha")]
    pool = [_verified_case(f"p{i}", f"m{i}") for i in range(5)]

    assert compose_case_set(hosts, target_files=6, donors_per_case=3) == []

    out = compose_case_set(hosts, target_files=6, donors_per_case=3, donor_pool=pool)
    assert len(out) == 1
    assert out[0]["metadata"]["distractors_added"] >= 3
    assert len(out[0]["context"]["files"]) == 6


def test_a_host_is_never_its_own_donor_even_when_it_is_in_the_pool():
    hosts = [_verified_case("shared", "alpha")]
    pool = [_verified_case("shared", "alpha"), _verified_case("other", "beta")]
    out = compose_case_set(hosts, target_files=6, donors_per_case=2, donor_pool=pool)
    assert out and "shared" not in out[0]["metadata"]["composed_from"]
