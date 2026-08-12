"""The predicates that reject material no downstream gate can catch.

Reverse construction's safety argument is that a model which writes bad tests produces a case
the validity gates reject. Three kinds of material slip through it, all measured on the shipped
``_revmixed`` set and the ``_rev_raw4`` draft pool:

* a suite that greps the implementation's source text — 12 of 31 cases, split JavaScript 11/14
  against Python 1/17. Source text always separates pre from post, so both gates pass.
* an edit that changed only comments — 4 of 91 deduplicated Python pairs once test files are
  already excluded. Not a defect fix.
* a test file as the thing under test — 35 of those same 91 pairs.

Each predicate is calibrated against the real set, so the tests below assert the calibration
rather than a hand-made example wherever the real numbers are what matters.
"""

import json

from aibench.extract.file_versions import defect_is_not_semantic, unsatisfiable_imports
from aibench.extract.reverse_case import iter_file_versions, reverse_case_from_versions
from aibench.models import Case
from aibench.validity import check_hidden_tests_are_inferable, check_test_reads_source


def _case(*, tests, impls=(("impl.py", "def f():\n    return 1\n"),), hidden=(), language="python"):
    files = [{"path": p, "content": c, "role": "impl"} for p, c in impls]
    files += [{"path": p, "content": c, "role": "test"} for p, c in tests]
    return Case.from_dict(
        {
            "case_id": "c1",
            "schema_version": "0.1",
            "task_type": "bugfix",
            "language": language,
            "prompt": "something is wrong with the output",
            "context": {"files": files},
            "grader": {
                "mode": "script",
                "command": "python -m pytest -q",
                "hidden_tests": [{"path": p, "content": c} for p, c in hidden],
            },
        }
    )


class TestReadsSourceText:
    def test_reflection_is_rejected(self):
        case = _case(
            tests=[
                (
                    "test_impl.py",
                    "import inspect, impl\ndef test_x():\n    inspect.getsource(impl.f)\n",
                )
            ]
        )
        assert [i.code for i in check_test_reads_source(case)] == ["test_reads_source_text"]

    def test_read_naming_the_implementation_is_rejected(self):
        case = _case(
            tests=[("t.js", "const s = readFileSync(join(__dirname, 'impl.js'), 'utf-8');\n")],
            impls=(("impl.js", "export const v = 1;\n"),),
            language="javascript",
        )
        assert check_test_reads_source(case)

    def test_read_plus_text_assertion_is_rejected(self):
        case = _case(
            tests=[
                ("t.js", "const source = readFileSync(p, 'utf-8');\nassert.match(source, /f/);\n")
            ],
            language="javascript",
        )
        assert check_test_reads_source(case)

    def test_hidden_tests_are_scanned_too(self):
        """A suite can hide the transcription in the part the solver never sees."""
        case = _case(
            tests=[("test_impl.py", "def test_ok():\n    assert True\n")],
            hidden=(
                (
                    "test_impl_spec.py",
                    "import inspect, impl\ndef test_y():\n    inspect.getsource(impl.f)\n",
                ),
            ),
        )
        assert check_test_reads_source(case)

    def test_writing_a_fixture_is_not_transcription(self):
        """Three of the six clean suites a loose pattern caught are excluded by write mode."""
        case = _case(
            tests=[
                (
                    "test_impl.py",
                    "def test_x():\n    with open('data.csv', 'w') as f:\n        f.write('a')\n",
                )
            ]
        )
        assert check_test_reads_source(case) == []

    def test_network_call_is_not_transcription(self):
        """`urlopen` and `os.fdopen` carry no word boundary before `open`, so they never match."""
        body = (
            "import urllib.request\n"
            "def test_health():\n"
            "    with urllib.request.urlopen('http://127.0.0.1:8000/api/health') as r:\n"
            "        assert r.status == 200\n"
        )
        assert check_test_reads_source(_case(tests=[("test_impl.py", body)])) == []

    def test_behaviour_test_is_kept(self):
        case = _case(
            tests=[("test_impl.py", "import impl\ndef test_f():\n    assert impl.f() == 1\n")]
        )
        assert check_test_reads_source(case) == []

    def test_read_without_a_text_assertion_is_kept(self):
        """Rule 3 is a conjunction. Reading a fixture and asserting on parsed values is fine."""
        body = (
            "const rows = readFileSync('data.csv','utf-8').split('\\n');\n"
            "assert.equal(parse(rows).length, 3);\n"
        )
        case = _case(tests=[("t.js", body)], impls=(("impl.js", "x\n"),), language="javascript")
        assert check_test_reads_source(case) == []

    def test_the_import_line_naming_the_impl_is_not_a_read(self):
        """Rule 2 is scoped to the read's own line.

        Matching the whole file would make every JavaScript test hit, because the import line
        necessarily names the implementation — 4 hits become 11 on the calibration set.
        """
        body = (
            "import { f } from './impl.js';\n"
            "const d = readFileSync('fixture.json','utf-8');\n"
            "assert.equal(f(d), 1);\n"
        )
        case = _case(tests=[("t.js", body)], impls=(("impl.js", "x\n"),), language="javascript")
        assert check_test_reads_source(case) == []

    def test_a_name_read_on_the_same_line_still_fires(self):
        """The other half of line scoping: `Path('impl.py').read_text()` names it before the call."""
        body = "src = Path('impl.py').read_text()\ndef test_x():\n    assert len(src) > 0\n"
        assert check_test_reads_source(_case(tests=[("test_impl.py", body)]))

    def test_a_single_letter_string_is_not_a_write_mode(self):
        """`readFileSync(p,'utf8').includes('a')` used to read as append mode and skip the call.

        Scanning the whole argument list for a quoted single letter meant one character in an
        assertion — 'foo' to 'a' — walked straight through an error-level gate.
        """
        body = "assert.ok(readFileSync(implPath,'utf8').includes('a'))\n"
        case = _case(tests=[("t.js", body)], impls=(("impl.js", "x\n"),), language="javascript")
        assert check_test_reads_source(case)

    def test_write_mode_behind_a_nested_call_is_still_a_write(self):
        """`open(os.path.join(d, 'o.txt'), 'w')` — the mode sits past the first `)`."""
        body = (
            "def test_x():\n"
            "    with open(os.path.join(d, 'o.txt'), 'w') as f:\n"
            "        f.write('a')\n"
            "    assert 'ok' in code\n"
        )
        assert check_test_reads_source(_case(tests=[("test_impl.py", body)])) == []

    def test_an_error_code_attribute_is_not_a_text_assertion(self):
        """`err.code.startsWith(...)` and `RE.match(x)` are ordinary; they used to fire rule 3."""
        body = (
            "const raw = readFileSync('fixture.json','utf-8');\n"
            "assert.ok(err.code.startsWith('E'));\n"
            "assert.ok(MAC_RE.match(mac));\n"
        )
        case = _case(tests=[("t.js", body)], impls=(("impl.js", "x\n"),), language="javascript")
        assert check_test_reads_source(case) == []


class TestDefectIsNotSemantic:
    def test_python_comment_and_layout_only(self):
        assert defect_is_not_semantic(
            "m.py", "# old\ndef f(x):\n    return x+1\n", "# new\ndef f(x):\n    return x + 1\n"
        )

    def test_python_docstring_only(self):
        assert defect_is_not_semantic(
            "m.py",
            'def f(x):\n    """Old."""\n    return x\n',
            'def f(x):\n    """New."""\n    return x\n',
        )

    def test_python_behaviour_change_is_kept(self):
        assert not defect_is_not_semantic(
            "m.py", "def f(x):\n    return x+1\n", "def f(x):\n    return x+2\n"
        )

    def test_js_comment_only(self):
        assert defect_is_not_semantic("a.ts", "// old\nconst v = 1;\n", "// new\nconst v = 1;\n")

    def test_a_regex_after_a_keyword_is_not_a_comment(self):
        """`return /^\\//` puts two slashes side by side; reading them as a comment deletes the line.

        It deletes it from both versions, so a real change to what survives is reported as
        comment-only. Here that is a `&&` becoming `||`.
        """
        pre = "function isAbs(p) {\n  return /^\\//.test(p) && a;\n}\n"
        post = "function isAbs(p) {\n  return /^\\//.test(p) || a;\n}\n"
        assert not defect_is_not_semantic("a.js", pre, post)

    def test_js_slashes_inside_a_string_are_not_a_comment(self):
        assert not defect_is_not_semantic(
            "a.ts", 'const s = "http://a";\n', 'const s = "http://b";\n'
        )
        assert defect_is_not_semantic(
            "a.ts", 'const s = "http://x"; // c1\n', 'const s = "http://x"; // c2\n'
        )

    def test_template_literal_whitespace_is_content(self):
        """Indentation inside a multi-line template is part of the value, not layout.

        73% of this corpus is TypeScript, much of it prompt and SQL templates, so reindenting
        one is an ordinary edit — and collapsing that whitespace reported it as no change.
        """
        assert not defect_is_not_semantic(
            "a.ts", "const q = `\n  SELECT *\n  FROM t`;\n", "const q = `\nSELECT *\nFROM t`;\n"
        )
        assert not defect_is_not_semantic(
            "a.ts", "export const m = `hello  \nworld`;\n", "export const m = `hello\nworld`;\n"
        )
        assert defect_is_not_semantic(
            "a.ts", "const s = `same`; // c1\n", "const s = `same`; // c2\n"
        )

    def test_js_behaviour_change_is_kept(self):
        assert not defect_is_not_semantic("a.ts", "const v = 1;\n", "const v = 2;\n")

    def test_unanalysable_input_is_kept(self):
        """Conservative on purpose: losing real material costs more than one bad candidate."""
        assert not defect_is_not_semantic("m.go", "package a\n", "package b\n")
        assert not defect_is_not_semantic("m.py", "def f(:\n", "def g(:\n")

    def test_an_unterminated_block_comment_is_not_analysable(self):
        """Scanning to end-of-file would report everything after the `/*` as commented out."""
        assert not defect_is_not_semantic(
            "a.js", "const v=1;\n/* oops\nconst w='A';\n", "const v=1;\n/* oops\nconst w='B';\n"
        )

    def test_deep_nesting_does_not_escape_as_recursion_error(self):
        """`iter_file_versions` runs outside cli.py's per-draft try, so this took down builds."""
        src = "s = " + '"a" + ' * 400 + '"a"\n'
        assert not defect_is_not_semantic("m.py", src, src.replace('"a"\n', '"b"\n'))


class TestRelativeImports:
    def test_relative_imports_are_unsatisfiable(self):
        """A flat one-file workspace has no package, so these raise before any test runs."""
        assert unsatisfiable_imports("m.py", "from .config import X") == {".config"}
        assert unsatisfiable_imports("m.py", "from ..pkg.mod import Y") == {"..pkg.mod"}
        assert unsatisfiable_imports("m.py", "from . import sibling") == {"."}

    def test_stdlib_still_satisfiable(self):
        assert unsatisfiable_imports("m.py", "import os\nfrom typing import Any") == set()

    def test_absolute_imports_are_unchanged(self):
        """The fix widened the pattern; dotted and indented absolute imports must still parse
        to their top-level package, not to the dotted path."""
        assert unsatisfiable_imports("m.py", "import os.path") == set()
        assert unsatisfiable_imports("m.py", "from a.b import c") == {"a"}
        assert unsatisfiable_imports("m.py", "    import nonexistent_pkg_xyz") == {
            "nonexistent_pkg_xyz"
        }


class TestWindowsPaths:
    def test_a_windows_test_path_reads_as_a_test(self):
        """66 of the 360 pairs in the draft pool carry backslashes.

        Splitting those on "/" alone returns the whole path, so `startswith("test_")` was false
        and the pool's Python test-file count read as 27 instead of 35 — with one file reaching
        the candidate set.
        """
        from aibench.languages import spec_for_path

        path = "D:\\lzy\\test\\agent-core\\test_workspace_header.py"
        assert spec_for_path(path).is_test_path(path)


class TestVisibleTestsAreProtected:
    def test_reverse_case_declares_its_visible_tests(self):
        """Every reverse case built so far shipped with `protected_paths` empty.

        That single empty field also disarms `detect_grading_interference`, which is only
        consulted for cases that opted in — so deleting the failing test, or dropping in a
        conftest, both passed. On the 24 of 31 cases with nothing hidden, the visible test file
        is the entire grading signal.
        """
        fv = {
            "path": "svc/calc.py",
            "pre": "def add(a, b):\n    return a - b\n",
            "post": "def add(a, b):\n    return a + b\n",
            "edits": 1,
        }
        answer = json.dumps(
            {
                "prompt": "把两个正整数相加时，得到的结果比预期小很多，有时甚至是负数；预期应当是两数之和。",
                "test_path": "test_calc.py",
                "test_content": (
                    "import calc\n"
                    "def test_a():\n    assert calc.add(1, 2) == 3\n"
                    "def test_b():\n    assert calc.add(0, 0) == 0\n"
                    "def test_c():\n    assert calc.add(5, 5) == 10\n"
                    "def test_d():\n    assert calc.add(2, 3) == 5\n"
                ),
            }
        )
        case = reverse_case_from_versions(
            fv, draft={"case_id": "d1", "prompt": "加法结果不对"}, chat=lambda *a, **k: answer
        )
        visible = [f["path"] for f in case["context"]["files"] if f["role"] == "test"]
        assert case["grader"]["protected_paths"] == visible
        assert visible


class TestIterFileVersionsPredicates:
    def _draft(self, versions):
        # Every pair claims a `pre` the trace read in full, so these tests exercise the
        # predicate under study rather than the provenance check that runs ahead of it.
        return {
            "metadata": {"file_versions": [{"pre_origin": "read_complete", **v} for v in versions]}
        }

    def test_test_files_are_dropped(self):
        draft = self._draft(
            [
                {
                    "path": "test_thing.py",
                    "pre": "def test_a():\n    assert 1\n",
                    "post": "def test_a():\n    assert 2\n",
                }
            ]
        )
        assert iter_file_versions(draft) == []

    def test_comment_only_edits_are_dropped(self):
        draft = self._draft([{"path": "m.py", "pre": "# a\nx = 1\n", "post": "# b\nx = 1\n"}])
        assert iter_file_versions(draft) == []

    def test_real_defect_survives(self):
        draft = self._draft(
            [
                {
                    "path": "m.py",
                    "pre": "def f():\n    return 1\n",
                    "post": "def f():\n    return 2\n",
                }
            ]
        )
        assert [fv["path"] for fv in iter_file_versions(draft)] == ["m.py"]


def _hidden_case(hidden_body: str, *, prompt: str = "Fix the export.") -> Case:
    return Case.from_dict(
        {
            "case_id": "h1",
            "schema_version": "0.1",
            "task_type": "bugfix",
            "language": "python",
            "prompt": prompt,
            "context": {
                "files": [
                    {
                        "path": "report.py",
                        "content": "def build_rows(data):\n    return list(data)\n",
                        "role": "impl",
                    },
                    {
                        "path": "test_report.py",
                        "content": "import report\n\n\ndef test_rows():\n    assert report.build_rows([]) == []\n",
                        "role": "test",
                    },
                ]
            },
            "grader": {
                "mode": "script",
                "command": "python -m pytest -q",
                "gold_files": [
                    {
                        "path": "report.py",
                        "content": "def build_rows(data):\n    return list(data)\n\n\ndef build_styles():\n    return {}\n",
                    }
                ],
                "hidden_tests": [{"path": "test_report_spec.py", "content": hidden_body}],
            },
            "metadata": {},
        }
    )


def test_a_hidden_test_may_pin_behaviour_of_a_visible_function():
    """Hiding behaviour is the point: the solver can see `build_rows` and has to get it right."""
    case = _hidden_case(
        "import report\n\n\ndef test_sorted():\n    assert report.build_rows([2, 1]) == [1, 2]\n"
    )
    assert check_hidden_tests_are_inferable(case) == []


def test_a_hidden_test_may_not_require_a_name_nothing_visible_mentions():
    """Measured on a real case: the hidden test called `create_shared_strings`, a name in
    neither the implementation, the visible tests, nor the prompt. Every solver fails it for
    the same reason, so it reads as a hard case while discriminating nothing."""
    case = _hidden_case(
        "import report\n\n\ndef test_styles():\n    assert report.build_styles() == {}\n"
    )
    issues = check_hidden_tests_are_inferable(case)
    assert [i.code for i in issues] == ["hidden_test_requires_unknowable_symbol"]
    assert "build_styles" in issues[0].message


def test_the_reference_solution_does_not_count_as_visible():
    """It is the one place the missing name is certain to appear -- that is what makes the case
    pass its solvability gate -- and it is exactly what the solver cannot read."""
    case = _hidden_case(
        "import report\n\n\ndef test_styles():\n    assert report.build_styles() == {}\n"
    )
    assert "build_styles" in (case.grader.gold_files[0].content or "")
    assert check_hidden_tests_are_inferable(case) != []


def test_a_name_the_prompt_introduces_is_inferable():
    case = _hidden_case(
        "import report\n\n\ndef test_styles():\n    assert report.build_styles() == {}\n",
        prompt="Add a build_styles() helper that returns the sheet styles.",
    )
    assert check_hidden_tests_are_inferable(case) == []


def test_a_filename_is_not_an_attribute_access():
    """`report.py` in a path or an error message reported that the test 'needs py'."""
    case = _hidden_case(
        "import report\n\n\ndef test_path():\n    assert 'report.py' in report.__file__\n"
    )
    assert check_hidden_tests_are_inferable(case) == []
