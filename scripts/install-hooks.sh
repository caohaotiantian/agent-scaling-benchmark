#!/usr/bin/env bash
# Install git pre-commit hooks (ruff format/import/lint).
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if command -v uv >/dev/null 2>&1; then
  uv sync --extra dev
  uv run pre-commit install
  echo "Installed pre-commit hook via: uv run pre-commit install"
  echo "Optional full-repo check: uv run pre-commit run --all-files"
else
  python -m pip install -e ".[dev]" -q
  pre-commit install
  echo "Installed pre-commit hook via: pre-commit install"
fi
