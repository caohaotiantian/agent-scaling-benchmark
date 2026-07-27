from __future__ import annotations

from pathlib import Path

from jsonschema import Draft202012Validator

from aibench.io_util import load_json, repo_root
from aibench.models import Case


def case_schema_path() -> Path:
    return repo_root() / "benchmarks/ai_coding/schemas/case.schema.json"


def case_set_dir(case_set: str) -> Path:
    """Resolve case set directory.

    Search order:
      1. benchmarks/ai_coding/cases/<set>  (production / auto-generated)
      2. tests/fixtures/case_sets/<set>    (unit-test fixtures only)
    """
    root = repo_root()
    primary = root / "benchmarks/ai_coding/cases" / case_set
    if primary.is_dir():
        return primary
    fixture = root / "tests/fixtures/case_sets" / case_set
    if fixture.is_dir():
        return fixture
    return primary


def load_schema_validator() -> Draft202012Validator:
    schema = load_json(case_schema_path())
    return Draft202012Validator(schema)


def iter_case_paths(case_set: str) -> list[Path]:
    d = case_set_dir(case_set)
    if not d.is_dir():
        raise FileNotFoundError(f"Case set not found: {d}")
    return sorted(d.glob("*.json"))


def load_cases(case_set: str, validate: bool = True) -> list[Case]:
    validator = load_schema_validator() if validate else None
    cases: list[Case] = []
    for path in iter_case_paths(case_set):
        raw = load_json(path)
        if validator is not None:
            errors = sorted(validator.iter_errors(raw), key=lambda e: list(e.path))
            if errors:
                msgs = "; ".join(e.message for e in errors[:5])
                raise ValueError(f"Schema validation failed for {path.name}: {msgs}")
        cases.append(Case.from_dict(raw))
    if not cases:
        raise ValueError(f"No cases in case set: {case_set}")
    return cases


def validate_case_set(case_set: str) -> list[str]:
    """Return list of error strings; empty means OK."""
    errors: list[str] = []
    try:
        paths = iter_case_paths(case_set)
    except FileNotFoundError as e:
        return [str(e)]
    if not paths:
        return [f"No JSON cases under {case_set}"]
    validator = load_schema_validator()
    seen_ids: set[str] = set()
    for path in paths:
        try:
            raw = load_json(path)
        except Exception as e:  # noqa: BLE001
            errors.append(f"{path.name}: invalid JSON ({e})")
            continue
        for err in sorted(validator.iter_errors(raw), key=lambda e: list(e.path)):
            errors.append(f"{path.name}: {err.message}")
        cid = raw.get("case_id")
        if isinstance(cid, str):
            if cid in seen_ids:
                errors.append(f"{path.name}: duplicate case_id {cid}")
            seen_ids.add(cid)
    return errors
