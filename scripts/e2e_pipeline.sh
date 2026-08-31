#!/usr/bin/env bash
# End-to-end production pipeline: DB → filter → generate → validate → ablation
# Defaults use real agents/models (no mock). Dry-run uses test fixtures only.
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
LIMIT=100
MAX_CASES=8
MATRIX="$ROOT/configs/runs/ablation-matrix.yaml"
DRAFT_DIR="$ROOT/benchmarks/ai_coding/cases/drafts-from-db"
KEPT_DIR="$ROOT/benchmarks/ai_coding/cases/drafts-kept"
CASE_SET_DIR="$ROOT/benchmarks/ai_coding/cases/auto-v0"
SKIP_EXTRACT=0
HEURISTIC_ONLY=0
STRICT_AUDIT=0
FORWARD=0
OUT_ROOT="$ROOT/runs"
WORKERS=""
TIER=""
MIN_TIER=""
CALIBRATE=0
REPEATS=3
ANCHORS="$ROOT/configs/runs/anchor-panel.yaml"
ABLATION_SET="auto-v0"
SELECTED_SET="disc-v0"
DIFFICULTY_QUOTA=""

usage() {
  cat <<EOF
Usage: $0 [options]

Production defaults:
  matrix=configs/runs/ablation-matrix.yaml  (openai_compat vs tool_loop, GLM-5.2)
  case_set=auto-v0  (no mock)

  --dry-run            Offline fixture path only (tests/fixtures mock matrix)
  --skip-extract       Reuse existing drafts in drafts-from-db
  --heuristic-only     Generate cases without LLM
  --strict-audit       Abort if any generated case fails an error-level validity gate
                       (default: annotate the verdict; \`ablation\` then excludes those cases)
  --forward            Use forward generation (the model invents the defect) instead of the
                       default reverse construction. Kept because three intervention
                       experiments judged the forward path ineffective and reproducing that
                       negative result needs the path to still exist — it is not a live option.
  --limit N            DB scan limit (default 100)
  --max-cases N        Max generated cases (default 8)
  --matrix PATH        Ablation matrix YAML
  --output-root PATH   Runs root
  --workers N          Passed to generate-cases (parallel generation)

Tiering (discrimination):
  --tier TN            Force a target tier for every draft (default: from trace signals)
  --min-tier TN        Drop generated cases that settle below this tier

Calibration (measures real discrimination; costs anchors x repeats full runs):
  --calibrate          Run calibrate-cases + select-cases, then ablate the selected set
  --repeats N          Repeats per anchor (default 3)
  --difficulty-quota S Band composition for select-cases as **shares summing to 1**, e.g.
                       "easy=0.15,mid=0.70,hard=0.15". Counts are rejected: `_validate_shares`
                       requires each value in (0, 1]. Without it select-cases takes whatever the
                       pool happens to contain, so the band mix is an accident of the sample.
  --anchors PATH       Anchor panel YAML (default configs/runs/anchor-panel.yaml)
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) DRY_RUN=1; shift ;;
    --skip-extract) SKIP_EXTRACT=1; shift ;;
    --heuristic-only) HEURISTIC_ONLY=1; shift ;;
    --strict-audit) STRICT_AUDIT=1; shift ;;
    --forward) FORWARD=1; shift ;;
    --limit) LIMIT="$2"; shift 2 ;;
    --max-cases) MAX_CASES="$2"; shift 2 ;;
    --matrix) MATRIX="$2"; shift 2 ;;
    --output-root) OUT_ROOT="$2"; shift 2 ;;
    --workers) WORKERS="$2"; shift 2 ;;
    --tier) TIER="$2"; shift 2 ;;
    --min-tier) MIN_TIER="$2"; shift 2 ;;
    --calibrate) CALIBRATE=1; shift ;;
    --repeats) REPEATS="$2"; shift 2 ;;
    --difficulty-quota) DIFFICULTY_QUOTA="$2"; shift 2 ;;
    --anchors) ANCHORS="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown: $1"; usage; exit 1 ;;
  esac
done

# Validated here, not at the point of use. `select-cases` runs inside an `if ... then ... else`
# so that "the calibration kept nothing" stays a legitimate verdict rather than a pipeline
# failure — which also swallows a malformed quota and prints it as that same verdict, after the
# expensive calibration has already been paid for.
if [[ -n "$DIFFICULTY_QUOTA" ]]; then
  if ! "${UV[@]}" python - "$DIFFICULTY_QUOTA" <<'VALIDATE_QUOTA'; then
import sys

from aibench.calibrate import _validate_shares, parse_tier_quota

# `parse_tier_quota` only parses. `_validate_shares` is what refuses counts, and it is reached
# deep inside `select-cases`, where the error is swallowed by the surrounding `if`.
try:
    _validate_shares(parse_tier_quota(sys.argv[1]), "difficulty quota")
except Exception as exc:
    print(f"bad --difficulty-quota {sys.argv[1]!r}: {exc}", file=sys.stderr)
    print(
        "shares are fractions summing to 1, e.g. easy=0.15,mid=0.70,hard=0.15",
        file=sys.stderr,
    )
    raise SystemExit(2) from None
VALIDATE_QUOTA
    exit 2
  fi
fi

echo "==> e2e pipeline dry_run=$DRY_RUN"

if [[ "$DRY_RUN" -eq 1 ]]; then
  echo "==> dry-run: fixtures only (not production)"
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

  "${UV[@]}" python -m aibench validate-cases --case-set e2e-demo
  # CI has no gateway: skip the default LLM second pass on `other`.
  "${UV[@]}" python -m aibench classify-cases \
    --case-set e2e-demo --annotate --no-llm-review \
    --report "$ROOT/.e2e-artifacts/problem_type.json"

  "${UV[@]}" python -m aibench ablation \
    --matrix "$ROOT/tests/fixtures/configs/runs/ablation-matrix.mock.yaml" \
    --case-set seed-v0 \
    --allow-weak-grader \
    --output-root "$OUT_ROOT/e2e-dry-run"

  echo "OK dry-run complete under $OUT_ROOT/e2e-dry-run"
  exit 0
fi

# Production path
if [[ "$SKIP_EXTRACT" -eq 0 ]]; then
  echo "==> extract-from-db limit=$LIMIT"
  mkdir -p "$DRAFT_DIR"
  "${UV[@]}" python -m aibench extract-from-db \
    --output-dir "$DRAFT_DIR" \
    --limit "$LIMIT" \
    --max-cases "$((MAX_CASES * 3))" \
    --require-gold \
    --require-edits
fi

echo "==> filter-drafts"
mkdir -p "$KEPT_DIR" "$ROOT/.e2e-artifacts"
"${UV[@]}" python -m aibench filter-drafts \
  --input-dir "$DRAFT_DIR" \
  --output-dir "$KEPT_DIR" \
  --dropped-dir "$ROOT/benchmarks/ai_coding/cases/drafts-dropped" \
  --report "$ROOT/.e2e-artifacts/filter_report.json"

echo "==> generate-cases"
mkdir -p "$CASE_SET_DIR"
GEN_FLAGS=(--input-dir "$KEPT_DIR" --output-dir "$CASE_SET_DIR" --max-cases "$MAX_CASES" --audit --secrets-scan)
# Reverse construction is the main line and is now what this pipeline runs. `--reverse` is a
# `store_true` flag, so the forward branch — where the model invents both the defect and the
# task, "inspired by this real user request (do NOT require the original repo)" — was the
# default, and `grep -rn reverse scripts/e2e_pipeline.sh` used to return nothing: the one-click
# pipeline could not even opt in. Forward generation was judged ineffective by three
# intervention experiments; the code stays so that result stays reproducible, behind --forward.
#
# Not added to the dry-run branch above, deliberately: `--reverse` always calls a live model
# (`cli.py` returns 1 without OPENAI_API_KEY/BASE_URL/MODEL and takes that branch before
# `--heuristic-only` is read), and the dry-run is what CI executes with no secrets.
if [[ "$FORWARD" -eq 1 ]]; then
  echo "==> WARNING: forward generation. Three intervention experiments found it produces no"
  echo "    difficulty signal: case size correlates +0.07, source-trace complexity |r| < 0.225"
  echo "    with the sign reversed, and suppressing prompt disclosure did nothing."
elif [[ "$HEURISTIC_ONLY" -eq 0 ]]; then
  GEN_FLAGS+=(--reverse)
fi
if [[ "$HEURISTIC_ONLY" -eq 1 ]]; then
  GEN_FLAGS+=(--heuristic-only)
fi
if [[ -n "$WORKERS" ]]; then
  GEN_FLAGS+=(--workers "$WORKERS")
fi
if [[ -n "$TIER" ]]; then
  GEN_FLAGS+=(--tier "$TIER")
fi
if [[ -n "$MIN_TIER" ]]; then
  GEN_FLAGS+=(--min-tier "$MIN_TIER")
fi
"${UV[@]}" python -m aibench generate-cases "${GEN_FLAGS[@]}"

echo "==> validate + audit auto-v0"
"${UV[@]}" python -m aibench validate-cases --case-set auto-v0
# `|| true` used to swallow an exception here as well as the verdict. The verdict is no longer
# lost either way: `--annotate` writes `validity_ok` back and `ablation` now excludes the cases
# that failed, so a partly-invalid set still produces an honest measurement on what is left.
# `--strict-audit` turns that into a hard stop for anyone who wants the pipeline to refuse.
AUDIT_FLAGS=(--case-set auto-v0 --annotate)
if [[ "$STRICT_AUDIT" -eq 1 ]]; then
  AUDIT_FLAGS+=(--fail-on-error)
fi
"${UV[@]}" python -m aibench audit-cases "${AUDIT_FLAGS[@]}"

echo "==> classify-cases (heuristic + LLM review of other)"
mkdir -p "$ROOT/.e2e-artifacts"
"${UV[@]}" python -m aibench classify-cases \
  --case-set auto-v0 --annotate \
  --report "$ROOT/.e2e-artifacts/problem_type.json"

if [[ "$CALIBRATE" -eq 1 ]]; then
  echo "==> calibrate-cases (anchors x $REPEATS runs; this is the expensive step)"
  "${UV[@]}" python -m aibench calibrate-cases \
    --case-set auto-v0 \
    --anchors "$ANCHORS" \
    --repeats "$REPEATS" \
    --output-root "$OUT_ROOT"

  CAL=$(ls -td "$OUT_ROOT"/calibration_* 2>/dev/null | head -1 || true)
  if [[ -z "$CAL" ]]; then
    echo "!! calibration produced no output; ablating auto-v0 unchanged"
  else
    echo "==> select-cases by discrimination -> $SELECTED_SET"
    # Keeping nothing is a legitimate verdict (e.g. every case is a giveaway), not a pipeline
    # failure — report it and ablate the unfiltered set rather than aborting under `set -e`.
    SELECT_FLAGS=(--calibration "$CAL/calibration.json" --from-set auto-v0 --to-set "$SELECTED_SET")
    if [[ -n "$DIFFICULTY_QUOTA" ]]; then
      SELECT_FLAGS+=(--difficulty-quota "$DIFFICULTY_QUOTA")
    fi
    if "${UV[@]}" python -m aibench select-cases "${SELECT_FLAGS[@]}"; then
      ABLATION_SET="$SELECTED_SET"
    else
      echo "!! calibration kept no discriminative case; ablating auto-v0 unfiltered"
      echo "   (see $CAL/calibration_report.md for the per-case reasons)"
    fi
    echo "---- calibration_report.md (head) ----"
    head -30 "$CAL/calibration_report.md" || true
  fi
fi

if [[ "$CALIBRATE" -ne 1 ]]; then
  echo "!! --calibrate was not passed: ablating a pool whose discrimination was never measured."
  echo "   Structural gates say a case *should* discriminate; only a calibration run says it does."
  echo "   The numbers below are still real measurements of the configurations, but the case set"
  echo "   behind them has no p_hat and no point-biserial, so 'this model is better' is not"
  echo "   supported by this run alone. Re-run with --calibrate to get that."
fi

echo "==> production ablation on $ABLATION_SET"
"${UV[@]}" python -m aibench ablation \
  --matrix "$MATRIX" \
  --case-set "$ABLATION_SET" \
  --baseline-experiment openai-compat-glm52 \
  --export-csv \
  --output-root "$OUT_ROOT"

echo "OK e2e production complete"
ABL=$(ls -td "$OUT_ROOT"/ablation_* 2>/dev/null | head -1 || true)
if [[ -n "$ABL" ]]; then
  echo "ablation_dir=$ABL"
  echo "---- ablation_report.md (head) ----"
  head -40 "$ABL/ablation_report.md" || true
fi
