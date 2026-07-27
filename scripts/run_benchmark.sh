#!/usr/bin/env bash
# One-click AI-Coding-Assist benchmark runner.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if command -v uv >/dev/null 2>&1; then
  UV=(uv run)
  # Ensure project env exists
  uv sync --quiet 2>/dev/null || uv pip install -e ".[dev]" --quiet
else
  UV=(python)
  export PYTHONPATH="${ROOT}/src${PYTHONPATH:+:$PYTHONPATH}"
  python -m pip install -e ".[dev]" -q 2>/dev/null || true
fi

AGENT_CFG="${ROOT}/configs/agents/mock.yaml"
MODEL_CFG="${ROOT}/configs/models/mock-model.yaml"
CASE_SET="auto-v0"
RUN_CONFIG="${ROOT}/configs/runs/seed-baseline.yaml"
RUN_ID=""

usage() {
  cat <<EOF
Usage: $0 [options]

Options:
  --agent PATH        Agent config YAML (default: mock)
  --model PATH        Model config YAML (default: mock-model)
  --case-set NAME     Case set under benchmarks/ai_coding/cases/ (default: auto-v0)
  --run-config PATH   Run config YAML
  --run-id ID         Optional run id
  -h, --help          Show help

Examples:
  $0
  $0 --agent configs/agents/openai_compat.yaml --model configs/models/openai-compat.example.yaml
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --agent) AGENT_CFG="$2"; shift 2 ;;
    --model) MODEL_CFG="$2"; shift 2 ;;
    --case-set) CASE_SET="$2"; shift 2 ;;
    --run-config) RUN_CONFIG="$2"; shift 2 ;;
    --run-id) RUN_ID="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown arg: $1"; usage; exit 1 ;;
  esac
done

# Resolve relative paths from repo root
[[ "$AGENT_CFG" != /* ]] && AGENT_CFG="$ROOT/$AGENT_CFG"
[[ "$MODEL_CFG" != /* ]] && MODEL_CFG="$ROOT/$MODEL_CFG"
[[ "$RUN_CONFIG" != /* ]] && RUN_CONFIG="$ROOT/$RUN_CONFIG"

echo "==> validate cases: $CASE_SET"
"${UV[@]}" python -m aibench validate-cases --case-set "$CASE_SET"

echo "==> run benchmark"
EXTRA=()
if [[ -n "$RUN_ID" ]]; then
  EXTRA+=(--run-id "$RUN_ID")
fi
"${UV[@]}" python -m aibench run \
  --run-config "$RUN_CONFIG" \
  --agent "$AGENT_CFG" \
  --model "$MODEL_CFG" \
  --case-set "$CASE_SET" \
  "${EXTRA[@]}"
