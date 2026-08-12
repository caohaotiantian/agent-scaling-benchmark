"""The grading environment is declared, and the declaration has to be true.

`unsatisfiable_imports` used to ask `importlib.find_spec` whether an import would resolve. That
answered a different question than the one being asked — what the developer's interpreter has,
not what the grader will have — and it resolved implicit namespace packages, so a trace's
`import src...` was satisfied by this repository's own `src/` whenever the root was importable.
The same draft pool survived 24 Python pairs when the predicate ran from the repository root and
21 from anywhere else: which cases got built depended on the working directory.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

from aibench.extract.file_versions import unsatisfiable_imports
from aibench.grading_env import is_available, provided, unsatisfied_promises
from aibench.io_util import repo_root

#: Manifest names whose distribution is called something else on PyPI.
DISTRIBUTION = {"yaml": "pyyaml", "av": "av"}

#: Declared because aibench itself depends on them and the grader shares this interpreter, so
#: they are not expected in the `grading` extra.
FROM_AIBENCH_RUNTIME = {
    "httpx",
    "jsonschema",
    "openpyxl",
    "pymysql",
    "pytest",
    "rich",
    "sqlalchemy",
    "yaml",
}


def _pyproject() -> dict:
    return tomllib.loads((repo_root() / "pyproject.toml").read_text(encoding="utf-8"))


def _requirement_names(specs: list[str]) -> set[str]:
    out = set()
    for spec in specs:
        name = spec.split(">=")[0].split("==")[0].split("[")[0].strip().lower()
        out.add(name)
    return out


class TestManifestMatchesInstall:
    def test_every_promise_is_importable(self):
        """A promise the interpreter cannot keep builds cases that die at grading time."""
        assert unsatisfied_promises() == []

    def test_declared_packages_are_installed_by_an_extra_or_by_aibench(self):
        """Nothing may be promised that no dependency declaration provides."""
        project = _pyproject()["project"]
        installed = _requirement_names(project["dependencies"])
        installed |= _requirement_names(project["optional-dependencies"]["grading"])
        for name in provided("python"):
            dist = DISTRIBUTION.get(name, name)
            assert dist in installed, f"{name} promised by the manifest but not declared anywhere"

    def test_grading_extra_is_fully_promised(self):
        """The reverse direction: an extra nobody declares is an install with no purpose."""
        extra = _requirement_names(_pyproject()["project"]["optional-dependencies"]["grading"])
        promised = {DISTRIBUTION.get(n, n) for n in provided("python")}
        assert extra <= promised

    def test_aibench_runtime_names_are_marked_as_such(self):
        """Keeps the two blocks of the manifest from drifting into one another."""
        assert provided("python") >= FROM_AIBENCH_RUNTIME


class TestAvailability:
    def test_stdlib_needs_no_declaration(self):
        assert is_available("python", "json")
        assert is_available("python", "importlib.util")

    def test_submodules_ride_on_the_top_level_name(self):
        assert is_available("python", "numpy.linalg")

    def test_undeclared_package_is_unavailable(self):
        assert not is_available("python", "obstacle_avoidance")

    def test_javascript_declares_nothing(self):
        assert provided("javascript") == frozenset()


class TestPredicateNoLongerConsultsTheMachine:
    def test_a_repository_directory_does_not_satisfy_an_import(self):
        """`find_spec("src")` succeeds from the repository root — a namespace package.

        That made the predicate's verdict depend on the working directory, and `src` and `tests`
        both occur as top-level imports in the corpus.
        """
        assert unsatisfiable_imports("m.py", "import src.helpers") == {"src"}
        assert unsatisfiable_imports("m.py", "import tests.conftest") == {"tests"}

    def test_an_installed_but_undeclared_package_is_still_unsatisfiable(self):
        """`ruff` is in the dev extra and importable here; the grader promises nothing of it."""
        assert unsatisfiable_imports("m.py", "import ruff") == {"ruff"}

    def test_a_declared_package_resolves(self):
        assert unsatisfiable_imports("m.py", "import numpy as np") == set()

    def test_relative_imports_remain_unsatisfiable(self):
        assert unsatisfiable_imports("m.py", "from .config import X") == {".config"}


def test_manifest_path_is_where_the_docs_say():
    assert (repo_root() / "configs" / "grading-env.yaml").is_file()
    assert Path("configs/grading-env.yaml").as_posix() == "configs/grading-env.yaml"
