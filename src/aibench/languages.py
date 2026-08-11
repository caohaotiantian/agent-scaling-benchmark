"""Per-language knowledge the tiering and grading machinery needs.

Everything that used to assume pytest lives here: how a test file is named, how a test
declaration is spelled, which command runs the suite, and how to read pass/fail counts back
out of its output. Adding a language means adding a :class:`LanguageSpec`, not editing
``tiers``, ``tier_shaping`` and ``grading`` in three places and hoping they agree.

Only languages this harness can actually execute are registered. A spec for a toolchain that
is not installed would let a case be generated, tiered and shipped, and only fail at grading
time — on every configuration equally, which reads as a hard case rather than a broken one.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class LanguageSpec:
    name: str
    aliases: tuple[str, ...]
    source_suffixes: tuple[str, ...]
    #: Matches a single line that opens a test, used to split visible tests from hidden ones.
    test_def: re.Pattern[str]
    #: Whole-suite command; must stay within the grader command whitelist.
    default_command: str
    #: (label -> count) pairs parsed from runner output.
    tally: re.Pattern[str]
    tally_groups: dict[str, str] = field(default_factory=dict)
    #: Exit codes that alone prove the runner never reached a verdict.
    uncollectable_exit_codes: tuple[int, ...] = ()
    #: A banner the runner prints only when it crashed loading a file — needed for runners
    #: that report a dead file as a failing test and so still print totals.
    uncollectable_output: re.Pattern[str] | None = None
    #: Basenames this runner discovers as tests. Left None where the shared convention below
    #: is right; set it wherever the runner has rules of its own.
    test_file: re.Pattern[str] | None = None
    #: Matches a grader command actually driven by this runner. The tally and exit codes below
    #: describe *this* runner, so a case that greps its own output or runs a bare script must
    #: not be judged by them.
    runner_command: re.Pattern[str] | None = None

    def drives(self, command: str | None) -> bool:
        return bool(self.runner_command and self.runner_command.search(command or ""))

    def tally_counts(self, output: str) -> dict[str, int]:
        """Passed / failed / errored test counts as the runner reported them."""
        counts = {"passed": 0, "failed": 0, "error": 0}
        for a, b in self.tally.findall(output or ""):
            # Specs differ on which capture group holds the number.
            label, number = (a, b) if not a.isdigit() else (b, a)
            key = self.tally_groups.get(label)
            if key:
                counts[key] = max(counts[key], int(number))
        return counts

    def parses(self, content: str) -> bool | None:
        """Whether ``content`` is syntactically valid, or ``None`` if this spec cannot tell.

        Only used to detect that a *transformation* broke a file that was fine before, so an
        honest "I don't know" is more useful than a guess.
        """
        if self.name != "python":
            return None
        try:
            compile(content, "<case>", "exec")
        except SyntaxError:
            return False
        except ValueError:
            # e.g. source containing a null byte — not a syntax verdict.
            return None
        return True

    def is_uncollectable(self, exit_code: int, output: str) -> bool:
        """Did the suite fail to *run*, rather than fail?

        A workspace that cannot be imported and a workspace whose tests fail are the same
        signal to ``returncode != 0``, so a case with a missing dependency is indistinguishable
        from a hard one — it fails for every configuration equally and reads as difficulty.

        Only ever asked about a run that already failed, so that a green run can never be
        reported as broken — a wholly skipped suite, an all-xfail suite and a ``true`` grader
        all exit 0 and must stay passes.

        The verdict comes from the runner's own tally rather than from error signatures in the
        text, because text matching gets it wrong in both directions: pytest counts a fixture's
        failed import as ``1 error`` at exit 1 with no collection banner, while a case that
        merely *asserts* on ``SyntaxError`` or ``ERR_MODULE_NOT_FOUND`` puts every signature a
        naive matcher looks for into an ordinary assertion diff.

        So a reported pass or failure settles it: the runner reached a verdict. Nothing having
        reached one covers an unresolved import, an unparseable file, a failed setup step and
        an interrupted run alike. The only text consulted is a crash banner that a runner emits
        exclusively when a file failed to load, for runners that report such a file as a
        failing test and therefore still print totals.
        """
        if exit_code in self.uncollectable_exit_codes:
            return True
        if self.uncollectable_output and self.uncollectable_output.search(output or ""):
            return True
        counts = self.tally_counts(output)
        # `error` is not consulted separately: pytest also counts teardown errors for tests
        # that ran and passed, so it is only evidence of non-collection when nothing else ran —
        # which the check below already covers.
        return not (counts["passed"] or counts["failed"])

    def is_test_path(self, path: str) -> bool:
        """Whether this runner would actually discover ``path`` as a test file.

        Discovery is per-runner, not a shared convention. ``test_app.js`` reads as a test to
        anyone who knows pytest and is silently never collected by ``node --test``: the suite
        exits 0 having run nothing, the stub gate sees a pass and reports ``stub_passed_grader``.
        Measured on an 11-case build, that accounted for all 7 JavaScript failures against 0 of
        4 Python ones.

        Backslashes are normalised first. Real traces carry Windows paths — 66 of the 360
        before/after pairs in the current draft pool — and splitting those on ``/`` alone yields
        the whole path, so ``D:\\lzy\\test\\agent-core\\test_workspace_header.py`` did not read
        as a test. That undercounted the test files in the Python pool by 8 (27 against 35) and
        let one through the extraction filter into the candidate set.
        """
        name = path.replace("\\", "/").rsplit("/", 1)[-1]
        if not path.endswith(self.source_suffixes):
            return False
        if self.test_file is not None:
            return bool(self.test_file.match(name))
        stem = name.rsplit(".", 1)[0]
        return (
            name.startswith("test_")
            or stem.endswith(("_test", ".test", ".spec", "_spec"))
            or stem == "test"
        )

    def test_filename(self, impl_name: str) -> str:
        """A test filename for ``impl_name`` that this runner is guaranteed to discover."""
        for suffix in self.source_suffixes:
            if impl_name.endswith(suffix):
                stem = impl_name[: -len(suffix)]
                candidate = (
                    f"test_{stem}{suffix}" if self.name == "python" else f"{stem}.test{suffix}"
                )
                if self.is_test_path(candidate):
                    return candidate
        return f"test_{impl_name}"

    def hidden_test_name(self, visible_name: str, *, marker: str = "spec") -> str:
        """Name for the hidden half of a split test file.

        It must still look like a test to the runner: node's `--test` discovers by filename
        pattern, so `clamp.test_spec.mjs` is silently never collected and the hidden half
        simply does not run — the case then passes on the smoke test alone, which is the exact
        failure the hidden tests exist to prevent.
        """
        for suffix in self.source_suffixes:
            if not visible_name.endswith(suffix):
                continue
            stem = visible_name[: -len(suffix)]
            # `clamp.test` -> `clamp_spec.test`, keeping the discoverable `.test<suffix>` tail.
            for tail in (".test", ".spec", "_test", "_spec"):
                if stem.endswith(tail):
                    return f"{stem[: -len(tail)]}_{marker}{tail}{suffix}"
            return f"{stem}_{marker}{suffix}"
        return f"{visible_name}_{marker}"


PYTHON = LanguageSpec(
    name="python",
    aliases=("py", "python3"),
    source_suffixes=(".py",),
    test_def=re.compile(r"^(?:async\s+)?def\s+(test_\w+)\s*\("),
    default_command="python -m pytest -q",
    tally=re.compile(r"(\d+)\s+(passed|failed|error|errors)\b"),
    tally_groups={"passed": "passed", "failed": "failed", "error": "error", "errors": "error"},
    # 3 internal error, 4 usage error, 5 nothing collected — none can be a verdict about the
    # code. 2 is deliberately absent: pytest also returns it when a *test* raises
    # KeyboardInterrupt, and a genuine collection error already shows up as `N error`.
    uncollectable_exit_codes=(3, 4, 5),
    # pytest's own default `python_files`. Verified: `clamp.spec.py` exits 5, "no tests ran".
    test_file=re.compile(r"^(?:test_.+|.+_test)\.py$"),
    runner_command=re.compile(r"-m\s+pytest\b"),
)

JAVASCRIPT = LanguageSpec(
    name="javascript",
    aliases=("js", "typescript", "ts", "node"),
    source_suffixes=(".js", ".mjs", ".cjs", ".ts"),
    # node:test style — `test('name', ...)` / `it('name', ...)`, optionally awaited.
    test_def=re.compile(r"^\s*(?:await\s+)?(?:test|it)\s*\(\s*[\"'`]([^\"'`]+)"),
    default_command="node --test",
    # node's default reporter prefixes its totals with U+2139; the TAP reporter uses '#'.
    tally=re.compile(r"^[#ℹ]\s+(pass|fail)\s+(\d+)", re.M),
    tally_groups={"pass": "passed", "fail": "failed"},
    # node exits 1 both for a failing test and for a module it could not load, and because
    # `--test` runs each file in its own subprocess a load failure is reported *as* a failing
    # test — totals and all — so the tally cannot separate them. The crash banner can: node
    # prints `Node.js v<version>` when the process died on an uncaught load error and never
    # inside an assertion diff. Matching the error codes themselves instead would condemn any
    # case whose test merely names one.
    uncollectable_exit_codes=(),
    # Known bound: a test that spawns a child node process which crashes has the child's
    # banner forwarded into this output, and is then read as a load failure. Nothing shipped
    # does that, and the misreading only ever rejects a case, never ships one.
    uncollectable_output=re.compile(r"^Node\.js v\d", re.M),
    # node --test discovers `*.test.*`, `*-test.*`, `*_test.*`, `test-*.*` and `test.*`, and
    # nothing else. Notably NOT `test_app.js`, which every pytest habit produces: verified
    # against node v24, `test_app.js` exits 0 having run no tests while `app.test.js` runs and
    # fails. A suite that runs nothing is a pass, so such a case dies on `stub_passed_grader`.
    test_file=re.compile(r"^(?:.+[.\-_]test|test|test[-.].+)\.(?:js|mjs|cjs|ts)$"),
    runner_command=re.compile(r"node\s+--test\b"),
)

LANGUAGES: tuple[LanguageSpec, ...] = (PYTHON, JAVASCRIPT)


def registered_spec(language: str | None) -> LanguageSpec | None:
    """Spec for ``language``, or ``None`` when this harness has no runner for it."""
    key = (language or "").strip().lower()
    for spec in LANGUAGES:
        if key == spec.name or key in spec.aliases:
            return spec
    return None


def spec_for(language: str | None) -> LanguageSpec:
    """Spec for a case's declared language, defaulting to Python."""
    return registered_spec(language) or PYTHON


def spec_for_path(path: str) -> LanguageSpec | None:
    for spec in LANGUAGES:
        if path.endswith(spec.source_suffixes):
            return spec
    return None


def count_test_functions(content: str, *, language: str | None = None) -> int:
    """Number of tests declared in a file.

    Without a language hint every spec is tried, because a case's declared language does not
    always match the file being inspected (a hidden test may be added before the language is
    settled).
    """
    specs = [spec_for(language)] if language else list(LANGUAGES)
    return max(
        (sum(1 for line in (content or "").splitlines() if s.test_def.match(line)) for s in specs),
        default=0,
    )


def pass_ratio(output: str, *, language: str | None = None) -> float | None:
    """Fraction of executed tests that passed, from a runner's summary output."""
    # An unspecified language keeps the Python default; a language that is named but has no
    # runner here does not, because a Python-shaped guess at a Gradle or Go summary is a
    # number with nothing behind it.
    if not (language or "").strip():
        spec = PYTHON
    elif (spec := registered_spec(language)) is None:
        return None
    counts = spec.tally_counts(output)
    total = sum(counts.values())
    return (counts["passed"] / total) if total else None


def describe() -> list[dict[str, Any]]:
    """Registered languages, for docs and diagnostics."""
    return [
        {
            "name": s.name,
            "aliases": list(s.aliases),
            "suffixes": list(s.source_suffixes),
            "command": s.default_command,
        }
        for s in LANGUAGES
    ]
