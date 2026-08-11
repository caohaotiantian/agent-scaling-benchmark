#!/usr/bin/env bash
# Format + import sort + lint Python with ruff.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if command -v uv >/dev/null 2>&1; then
  RUFF=(uv run ruff)
else
  RUFF=(ruff)
fi

TARGETS=(src tests scripts)
if [[ $# -gt 0 ]]; then
  TARGETS=("$@")
fi

echo "==> ruff check --fix (lint + imports)"
"${RUFF[@]}" check --fix "${TARGETS[@]}"

echo "==> ruff format"
"${RUFF[@]}" format "${TARGETS[@]}"

echo "==> ruff check (verify)"
"${RUFF[@]}" check "${TARGETS[@]}"

echo "OK: ruff format + lint clean"
