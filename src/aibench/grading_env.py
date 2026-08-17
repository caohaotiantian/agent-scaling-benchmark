"""What the grading environment provides, declared rather than discovered.

`unsatisfiable_imports` used to answer "can this import resolve?" with `importlib.find_spec`,
which asks the interpreter that happens to be running. Two things were wrong with that. It
resolves implicit namespace packages, so any directory on `sys.path` satisfies an import of the
same name — a trace's `import src...` was answered by this project's own `src/`, and the same
draft pool yielded 24 surviving Python pairs from the repository root against 21 from anywhere
else. And it reports what the *developer's* machine has installed, which is not what the grader
will have.

The manifest in ``configs/grading-env.yaml`` replaces both. It is a promise, so
:func:`unsatisfied_promises` exists to check it: a name declared here but not importable would
let material through the extraction predicate and then fail at grading, which reads as
difficulty rather than as a missing package.
"""

from __future__ import annotations

import functools
import importlib.util
import sys
from pathlib import Path
from typing import Any

from aibench.io_util import load_yaml, repo_root

DEFAULT_MANIFEST = "configs/grading-env.yaml"


@functools.lru_cache(maxsize=8)
def _load(path: str) -> dict[str, tuple[str, ...]]:
    data: Any = load_yaml(Path(path) if Path(path).is_absolute() else repo_root() / path)
    out: dict[str, tuple[str, ...]] = {}
    for language, names in (data or {}).items():
        out[str(language)] = tuple(sorted({str(n) for n in (names or [])}))
    return out


def provided(language: str, *, manifest: str = DEFAULT_MANIFEST) -> frozenset[str]:
    """Third-party top-level names the grading environment promises for ``language``."""
    return frozenset(_load(manifest).get(language, ()))


def is_available(language: str, module: str, *, manifest: str = DEFAULT_MANIFEST) -> bool:
    """Whether ``module`` resolves at grading time, by declaration only.

    The top-level name is what matters: declaring ``numpy`` covers ``numpy.linalg``, because
    installing the distribution brings the submodules with it.
    """
    top = module.split(".")[0]
    if language == "python" and top in sys.stdlib_module_names:
        return True
    return top in provided(language, manifest=manifest)


def unsatisfied_promises(*, manifest: str = DEFAULT_MANIFEST) -> list[str]:
    """Python names this manifest promises that the running interpreter cannot import.

    Only Python is checkable here — a JavaScript name would need the grader's `node_modules`,
    and the manifest declares none.
    """
    missing: list[str] = []
    for name in sorted(provided("python", manifest=manifest)):
        try:
            if importlib.util.find_spec(name) is None:
                missing.append(name)
        except (ImportError, ValueError):
            missing.append(name)
    return missing


def grading_env_digest(*, manifest: str = DEFAULT_MANIFEST) -> str:
    """Content hash of the manifest plus the installed version of every name it promises.

    A verdict is only attributable if the environment that produced it is. Two runs of the same
    case set can disagree because one had numpy 2.1 and the other 2.3, and nothing in any
    artifact recorded which — the manifest says *which names* are promised, never which builds
    satisfied them.

    A name the interpreter cannot import is hashed as ``absent``, so a missing promise changes
    the digest rather than silently producing the same one as a satisfied environment.
    """
    import hashlib
    from importlib import metadata

    parts = [manifest]
    for language in sorted(_load(manifest)):
        for name in sorted(provided(language, manifest=manifest)):
            version = "absent"
            if language == "python":
                try:
                    version = metadata.version(name)
                except metadata.PackageNotFoundError:
                    version = (
                        "importable"
                        if is_available(language, name, manifest=manifest)
                        else "absent"
                    )
            parts.append(f"{language}:{name}:{version}")
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()[:16]
