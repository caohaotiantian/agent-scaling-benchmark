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

    def is_test_path(self, path: str) -> bool:
        name = path.rsplit("/", 1)[-1]
        if not path.endswith(self.source_suffixes):
            return False
        stem = name.rsplit(".", 1)[0]
        return (
            name.startswith("test_")
            or stem.endswith(("_test", ".test", ".spec", "_spec"))
            or stem == "test"
        )

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
)

LANGUAGES: tuple[LanguageSpec, ...] = (PYTHON, JAVASCRIPT)


def spec_for(language: str | None) -> LanguageSpec:
    """Spec for a case's declared language, defaulting to Python."""
    key = (language or "").strip().lower()
    for spec in LANGUAGES:
        if key == spec.name or key in spec.aliases:
            return spec
    return PYTHON


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
    spec = spec_for(language)
    counts = {"passed": 0, "failed": 0, "error": 0}
    for a, b in spec.tally.findall(output or ""):
        # Specs differ on which capture group holds the number.
        label, number = (a, b) if not a.isdigit() else (b, a)
        key = spec.tally_groups.get(label)
        if key:
            counts[key] = max(counts[key], int(number))
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
