#!/usr/bin/env bash
# Production one-click AI-Coding-Assist benchmark runner (no mock defaults).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ -f "$ROOT/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$ROOT/.env"
  set +a
fi

if command -v uv >/dev/null 2>&1; then
  UV=(uv run)
  # Not a bare `uv sync`: that removes the grading extra before every production run, and
  # `configs/grading-env.yaml` promises those packages to the grader.
  uv sync --extra dev --extra grading --quiet 2>/dev/null || true
else
  UV=(python)
  export PYTHONPATH="${ROOT}/src${PYTHONPATH:+:$PYTHONPATH}"
fi

AGENT_CFG="${ROOT}/configs/agents/openai_compat.yaml"
MODEL_CFG="${ROOT}/configs/models/glm52.yaml"
CASE_SET="auto-v0"
RUN_CONFIG="${ROOT}/configs/runs/baseline.yaml"
RUN_ID=""
WORKERS=""

usage() {
  cat <<EOF
Usage: $0 [options]

Production defaults (no mock):
  agent=openai_compat  model=glm52  case_set=auto-v0  run=baseline.yaml

Options:
  --agent PATH        Agent config YAML
  --model PATH        Model config YAML
  --case-set NAME     Case set name (default: auto-v0)
  --run-config PATH   Run config YAML
  --run-id ID         Optional run id
  --workers N         Case-level parallelism
  -h, --help          Show help

Requires .env: OPENAI_API_KEY, OPENAI_BASE_URL (and auto-v0 cases for eval)
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --agent) AGENT_CFG="$2"; shift 2 ;;
    --model) MODEL_CFG="$2"; shift 2 ;;
    --case-set) CASE_SET="$2"; shift 2 ;;
    --run-config) RUN_CONFIG="$2"; shift 2 ;;
    --run-id) RUN_ID="$2"; shift 2 ;;
    --workers) WORKERS="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown arg: $1"; usage; exit 1 ;;
  esac
done

[[ "$AGENT_CFG" != /* ]] && AGENT_CFG="$ROOT/$AGENT_CFG"
[[ "$MODEL_CFG" != /* ]] && MODEL_CFG="$ROOT/$MODEL_CFG"
[[ "$RUN_CONFIG" != /* ]] && RUN_CONFIG="$ROOT/$RUN_CONFIG"

echo "==> validate cases: $CASE_SET"
"${UV[@]}" python -m aibench validate-cases --case-set "$CASE_SET"

EXTRA=()
if [[ -n "$RUN_ID" ]]; then EXTRA+=(--run-id "$RUN_ID"); fi
if [[ -n "$WORKERS" ]]; then EXTRA+=(--workers "$WORKERS"); fi

echo "==> run benchmark"
"${UV[@]}" python -m aibench run \
  --run-config "$RUN_CONFIG" \
  --agent "$AGENT_CFG" \
  --model "$MODEL_CFG" \
  --case-set "$CASE_SET" \
  "${EXTRA[@]}"
