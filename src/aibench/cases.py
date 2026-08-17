from __future__ import annotations

from pathlib import Path

from jsonschema import Draft202012Validator

from aibench.io_util import load_json, repo_root
from aibench.models import Case


def case_schema_path() -> Path:
    return repo_root() / "benchmarks/ai_coding/schemas/case.schema.json"


#: Names a set explicitly as a committed fixture: `fixture:seed-v0` resolves to
#: `tests/fixtures/case_sets/seed-v0` and never anywhere else.
FIXTURE_PREFIX = "fixture:"

#: The mirror escape: `generated:seed-v0` resolves to the corpus root and never to the fixture.
GENERATED_PREFIX = "generated:"

#: Overrides where generated case sets live. Exists so a test can put the whole namespace under
#: its `tmp_path` instead of writing `_test_*` directories into the checkout, which several
#: tests did — and which is one deletion away from the incident `.gitignore` records.
CASE_ROOT_ENV = "AIBENCH_CASE_ROOT"


def case_set_root() -> Path:
    import os

    override = os.environ.get(CASE_ROOT_ENV)
    return Path(override) if override else repo_root() / "benchmarks/ai_coding/cases"


def fixture_case_set_root() -> Path:
    return repo_root() / "tests/fixtures/case_sets"


def case_root_is_overridden() -> bool:
    """Whether the operator pointed the corpus root *somewhere else* deliberately.

    Pointing it at the default location is not an override — `AIBENCH_CASE_ROOT` set to
    `benchmarks/ai_coding/cases` used to switch the fixture-shadow guard off entirely while
    changing nothing about where cases are read from.
    """
    import os

    override = os.environ.get(CASE_ROOT_ENV)
    if not override:
        return False
    default = repo_root() / "benchmarks/ai_coding/cases"
    try:
        return Path(override).resolve() != default.resolve()
    except OSError:
        return True


def case_set_dir(case_set: str) -> Path:
    """Resolve case set directory.

    Search order:
      1. ``fixture:<name>``                → tests/fixtures/case_sets/<name>, unconditionally
      2. ``generated:<name>``              → the corpus root, unconditionally
      3. ``$AIBENCH_CASE_ROOT``/<set>      (default: benchmarks/ai_coding/cases/<set>)
      4. tests/fixtures/case_sets/<set>    (unit-test fixtures only)

    The generated location wins over the fixture, which is right for a name like `auto-v0` and
    dangerous for a name the test suite asserts against: a local `benchmarks/ai_coding/cases/
    seed-v0` would silently replace the four committed cases every `seed-v0` assertion is
    written for, and nothing would say so. Shadowing a fixture name therefore raises rather
    than resolving — but only when the corpus root is the default one, and both escapes exist:
    `fixture:` for the committed cases, `generated:` for the shadowing set. A bare raise whose
    only remedy is "rename it" is a dead end on the two commands most likely to hit it,
    `promote` and `calibrate`.

    ``$AIBENCH_CASE_ROOT`` suppresses the check entirely. Pointing the root at a scratch
    directory is how an operator asks for an isolated corpus, and refusing `seed-v0` there —
    because a fixture of that name exists in the checkout — made the override unusable for
    exactly the sets the suite cares about.
    """
    if case_set.startswith(FIXTURE_PREFIX):
        return fixture_case_set_root() / case_set[len(FIXTURE_PREFIX) :]
    if case_set.startswith(GENERATED_PREFIX):
        return case_set_root() / case_set[len(GENERATED_PREFIX) :]
    primary = case_set_root() / case_set
    fixture = fixture_case_set_root() / case_set
    if primary.is_dir():
        if fixture.is_dir() and not case_root_is_overridden():
            raise ValueError(
                f"case set {case_set!r} exists both at {primary} and as a committed fixture at "
                f"{fixture}. The generated one would win and silently replace what the test "
                f"suite asserts against. Ask for {FIXTURE_PREFIX}{case_set} or "
                f"{GENERATED_PREFIX}{case_set} to say which you mean, set "
                f"{CASE_ROOT_ENV} to an isolated root, or rename it."
            )
        return primary
    if fixture.is_dir():
        return fixture
    return primary


def load_schema_validator() -> Draft202012Validator:
    schema = load_json(case_schema_path())
    return Draft202012Validator(schema)


def is_case_json_path(path: Path) -> bool:
    """True for real case files; skip sidecar reports like ``_secrets_scan.json``."""
    name = path.name
    return path.suffix == ".json" and not name.startswith("_")


#: Sets that a clone does have, because they are committed as test fixtures.
_FIXTURE_SETS = ("seed-v0", "_t4fixture")


def missing_case_set_message(case_set: str, directory: Path) -> str:
    """Say what to do about it, not only that it is absent.

    `aibench run --case-set auto-v0` in a fresh clone used to die on a bare `FileNotFoundError`
    naming a path — which is true and useless, because no case set is in the repository at all
    and the reader has no way to know that from the message. The sets are gitignored by an
    incident-motivated policy, so "not found" is the *expected* state here rather than a
    misconfiguration.
    """
    return (
        f"Case set not found: {directory}\n"
        f"No case set ships with this repository: `.gitignore` excludes "
        f"`benchmarks/ai_coding/cases/*/` by shape, after a `git add -A` once committed 3,551 "
        f"files of production source including a live key.\n"
        f"  build one:   uv run python -m aibench generate-cases --reverse "
        f"--input-dir <drafts> --output-dir benchmarks/ai_coding/cases/{case_set}\n"
        f"  or the whole pipeline:  ./scripts/e2e_pipeline.sh\n"
        f"  offline, with the committed fixtures:  --case-set {_FIXTURE_SETS[0]}\n"
        f"  offline smoke test:  ./scripts/e2e_pipeline.sh --dry-run"
    )


def iter_case_paths(case_set: str) -> list[Path]:
    d = case_set_dir(case_set)
    if not d.is_dir():
        raise FileNotFoundError(missing_case_set_message(case_set, d))
    return sorted(p for p in d.glob("*.json") if is_case_json_path(p))


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
        except Exception as e:
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
