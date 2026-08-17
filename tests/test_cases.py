from pathlib import Path

from aibench.cases import case_set_dir, is_case_json_path, load_cases, validate_case_set


def test_seed_case_set_valid():
    assert validate_case_set("seed-v0") == []


def test_load_seed_cases():
    cases = load_cases("seed-v0")
    assert len(cases) >= 3
    ids = {c.case_id for c in cases}
    assert "seed-v0-001-fizzbuzz" in ids


def test_is_case_json_path_skips_sidecars():
    assert is_case_json_path(Path("foo.json"))
    assert not is_case_json_path(Path("_secrets_scan.json"))
    assert not is_case_json_path(Path("notes.txt"))


def test_validate_ignores_underscore_sidecar(tmp_path, monkeypatch):
    """generate --secrets-scan writes _secrets_scan.json next to cases; must not fail validate."""
    import shutil

    from aibench import cases as cases_mod
    from aibench.cases import CASE_ROOT_ENV
    from aibench.io_util import write_json

    # A copy under `tmp_path`, never the committed fixture. Writing a sidecar into
    # `tests/fixtures/case_sets/seed-v0` and unlinking it in a `finally` leaves the file behind
    # whenever the process dies in between, and the fixture is what every `seed-v0` assertion
    # in the suite is written against.
    root = tmp_path / "cases"
    shutil.copytree(case_set_dir("seed-v0"), root / "seed-v0")
    monkeypatch.setenv(CASE_ROOT_ENV, str(root))

    write_json(
        root / "seed-v0" / "_secrets_scan.json",
        {
            "directory": str(root / "seed-v0"),
            "files_scanned": 1,
            "finding_count": 0,
            "clean": True,
            "findings": [],
        },
    )
    assert validate_case_set("seed-v0") == []
    assert all(is_case_json_path(p) for p in cases_mod.iter_case_paths("seed-v0"))
