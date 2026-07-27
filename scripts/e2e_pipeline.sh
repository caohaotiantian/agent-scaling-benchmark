#!/usr/bin/env bash
# End-to-end: (optional DB extract) → filter → generate → validate → ablation
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
else
  UV=(python)
  export PYTHONPATH="${ROOT}/src${PYTHONPATH:+:$PYTHONPATH}"
fi

DRY_RUN=0
LIMIT=80
MAX_CASES=8
MATRIX="$ROOT/configs/runs/ablation-matrix.mock.yaml"
DRAFT_DIR="$ROOT/benchmarks/ai_coding/cases/drafts-from-db"
KEPT_DIR="$ROOT/benchmarks/ai_coding/cases/drafts-kept"
CASE_SET_DIR="$ROOT/benchmarks/ai_coding/cases/auto-v0"
SKIP_EXTRACT=0
HEURISTIC_ONLY=0
OUT_ROOT="$ROOT/runs"

usage() {
  cat <<EOF
Usage: $0 [options]

  --dry-run            Use fixtures only (no DB/LLM); mock ablation on seed-v0
  --skip-extract       Reuse existing drafts in drafts-from-db
  --heuristic-only     Generate cases without LLM
  --limit N            DB scan limit (default 80)
  --max-cases N        Max generated cases (default 8)
  --matrix PATH        Ablation matrix YAML
  --output-root PATH   Runs root
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) DRY_RUN=1; shift ;;
    --skip-extract) SKIP_EXTRACT=1; shift ;;
    --heuristic-only) HEURISTIC_ONLY=1; shift ;;
    --limit) LIMIT="$2"; shift 2 ;;
    --max-cases) MAX_CASES="$2"; shift 2 ;;
    --matrix) MATRIX="$2"; shift 2 ;;
    --output-root) OUT_ROOT="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown: $1"; usage; exit 1 ;;
  esac
done

echo "==> e2e pipeline dry_run=$DRY_RUN"

if [[ "$DRY_RUN" -eq 1 ]]; then
  echo "==> dry-run: fixture extract + filter + heuristic generate + mock ablation"
  FIX_DRAFT="$ROOT/.e2e-artifacts/drafts"
  FIX_KEPT="$ROOT/.e2e-artifacts/kept"
  FIX_CASES="$ROOT/benchmarks/ai_coding/cases/e2e-demo"
  rm -rf "$ROOT/.e2e-artifacts" "$FIX_CASES"
  mkdir -p "$FIX_DRAFT" "$FIX_KEPT" "$FIX_CASES"

  "${UV[@]}" python -m aibench extract-cases \
    --input "$ROOT/tests/fixtures/sessions_min.json" \
    --output-dir "$FIX_DRAFT" \
    --max-cases 10

  "${UV[@]}" python -m aibench filter-drafts \
    --input-dir "$FIX_DRAFT" \
    --output-dir "$FIX_KEPT" \
    --report "$ROOT/.e2e-artifacts/filter_report.json"

  "${UV[@]}" python -m aibench generate-cases \
    --input-dir "$FIX_KEPT" \
    --output-dir "$FIX_CASES" \
    --heuristic-only \
    --max-cases "$MAX_CASES"

  # Case set name = directory name under cases/
  "${UV[@]}" python -m aibench validate-cases --case-set e2e-demo

  "${UV[@]}" python -m aibench ablation \
    --matrix "$MATRIX" \
    --case-set seed-v0 \
    --output-root "$OUT_ROOT/e2e-dry-run"

  echo "OK dry-run complete. Ablation under $OUT_ROOT/e2e-dry-run"
  ls -la "$OUT_ROOT/e2e-dry-run" | head -20
  exit 0
fi

# Full path (DB + optional LLM)
if [[ "$SKIP_EXTRACT" -eq 0 ]]; then
  echo "==> extract-from-db limit=$LIMIT"
  mkdir -p "$DRAFT_DIR"
  "${UV[@]}" python -m aibench extract-from-db \
    --output-dir "$DRAFT_DIR" \
    --limit "$LIMIT" \
    --max-cases "$((MAX_CASES * 3))" \
    --require-gold
fi

echo "==> filter-drafts"
mkdir -p "$KEPT_DIR"
"${UV[@]}" python -m aibench filter-drafts \
  --input-dir "$DRAFT_DIR" \
  --output-dir "$KEPT_DIR" \
  --dropped-dir "$ROOT/benchmarks/ai_coding/cases/drafts-dropped" \
  --report "$ROOT/.e2e-artifacts/filter_report.json"

echo "==> generate-cases"
mkdir -p "$CASE_SET_DIR" "$ROOT/.e2e-artifacts"
GEN_FLAGS=(--input-dir "$KEPT_DIR" --output-dir "$CASE_SET_DIR" --max-cases "$MAX_CASES")
if [[ "$HEURISTIC_ONLY" -eq 1 ]]; then
  GEN_FLAGS+=(--heuristic-only)
fi
"${UV[@]}" python -m aibench generate-cases "${GEN_FLAGS[@]}"

echo "==> validate auto-v0"
"${UV[@]}" python -m aibench validate-cases --case-set auto-v0

echo "==> ablation"
"${UV[@]}" python -m aibench ablation \
  --matrix "$MATRIX" \
  --case-set auto-v0 \
  --output-root "$OUT_ROOT"

echo "OK e2e complete"
