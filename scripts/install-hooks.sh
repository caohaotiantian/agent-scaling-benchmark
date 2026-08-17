#!/usr/bin/env bash
# Install git pre-commit hooks (ruff format/import/lint).
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if command -v uv >/dev/null 2>&1; then
  # Both extras, always. A bare `uv sync --extra dev` *removes* the grading extra, and this
  # script is the README's very next line after the install command — so following the
  # README in order used to leave numpy, pandas, matplotlib, flask, requests and av
  # uninstalled and `test_every_promise_is_importable` red.
  uv sync --extra dev --extra grading
  uv run pre-commit install
  echo "Installed pre-commit hook via: uv run pre-commit install"
  echo "Optional full-repo check: uv run pre-commit run --all-files"
else
  python -m pip install -e ".[dev,grading]" -q
  pre-commit install
  echo "Installed pre-commit hook via: pre-commit install"
fi
