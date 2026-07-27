# Implementation: validity + parallelism

| 字段 | 值 |
|------|-----|
| Design | `docs/design/2026-07-27-validity-and-parallelism.md` |
| TEST-CMD | `uv run pytest tests/ -q` |
| Status | closed |
| Closed-on | 2026-07-27 |

## Phases

### Phase 1 — Validity + stats
- `src/aibench/validity.py` audit_case / audit_case_set
- `src/aibench/stats.py` wilson_ci, stratify
- report.build_summary 写入 CI + stratified
- CLI `audit-cases`
- tests/test_validity.py

ACCEPT: `uv run pytest tests/test_validity.py tests/test_runner.py -q`

### Phase 2 — Parallel run + generate
- runner `case_workers`
- generate-cases `--workers`
- CLI/run-config 透传
- tests/test_stats_parallel.py

ACCEPT: `uv run pytest tests/ -q`
