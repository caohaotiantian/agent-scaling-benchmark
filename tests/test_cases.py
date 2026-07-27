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
    from aibench import cases as cases_mod
    from aibench.io_util import write_json

    # Use seed-v0 dir and add a sidecar without polluting fixtures permanently
    d = case_set_dir("seed-v0")
    sidecar = d / "_secrets_scan.json"
    wrote = False
    try:
        if not sidecar.exists():
            write_json(
                sidecar,
                {
                    "directory": str(d),
                    "files_scanned": 1,
                    "finding_count": 0,
                    "clean": True,
                    "findings": [],
                },
            )
            wrote = True
        assert validate_case_set("seed-v0") == []
        assert all(is_case_json_path(p) for p in cases_mod.iter_case_paths("seed-v0"))
    finally:
        if wrote and sidecar.exists():
            sidecar.unlink()
