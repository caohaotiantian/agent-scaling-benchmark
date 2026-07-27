# Implementation: E2E session → benchmark → ablation

| 字段 | 值 |
|------|-----|
| Design | `docs/design/2026-07-25-e2e-session-to-benchmark.md` |
| Slug | `2026-07-25-e2e-session-to-benchmark` |
| TEST-CMD | `uv run pytest tests/ -q` |

## 1. Task Index

| Deliverable | Design § | AC |
|-------------|----------|-----|
| env 加载 + glm 配置 | D6/D7 P0, §5 | AC3 |
| 规则筛选器 | D2, Deliverable 1 | AC1 |
| AI/启发式生成器 | D3 | AC2, AC8 |
| 晋升 auto-v0 | D4 | AC2 |
| 消融 runner + 聚合 | D5 | AC4, AC5 |
| e2e_pipeline.sh | D6 | AC6 |
| 测试全绿 | §7 | AC7 |

## 2. Phase Breakdown

### Phase 1 — Config, filter, generate (core pipeline)

**Entry**: design L1 accepted (or main proceeds with design draft per user continue).  
**Exit**: filter + generate modules + tests green; CLI subcommands work.

#### Tasks (TDD order)

1. **Test** `tests/test_env_config.py`: loading dotenv from repo `.env` without committing secrets; missing file OK.
2. **Impl** `src/aibench/env_config.py`: `load_dotenv()`, helpers for OpenAI settings.
3. **Test** `tests/test_filter.py`: drop noisy prompts (disk health, judge score); keep coding intents; fixture from review-pack text.
4. **Impl** `src/aibench/extract/filter_rules.py`: `FilterDecision`, `rule_filter_draft(draft|prompt,meta)`.
5. **Test** `tests/test_generate.py`: heuristic generate from minimal session fixture → schema-valid case with script or gold grader.
6. **Impl** `src/aibench/extract/generate_case.py`: heuristic + optional LLM path (`generate_case_with_llm`), command whitelist.
7. **Impl** CLI: `filter-drafts`, `generate-cases`; wire `load_dotenv` at CLI start.
8. **Config** `configs/models/glm52.yaml` reading env model name pattern.

**ACCEPT-CMD**:

```bash
uv run pytest tests/test_env_config.py tests/test_filter.py tests/test_generate.py -q
uv run python -m aibench filter-drafts --help
uv run python -m aibench generate-cases --help
```

### Phase 2 — Ablation + e2e script

**Entry**: Phase 1 exit.  
**Exit**: ablation matrix mock run produces multi-row report; e2e dry-run OK.

#### Tasks (TDD order)

1. **Test** `tests/test_ablation.py`: matrix with 2 mock runs → ablation_summary has 2 rows; report markdown has 综述表.
2. **Impl** `src/aibench/ablation.py` + CLI `ablation`.
3. **Config** `configs/runs/ablation-matrix.mock.yaml`.
4. **Impl** `scripts/e2e_pipeline.sh` with `--dry-run` (uses seed/heuristic fixtures, mock matrix).
5. **Test** extend runner if needed for matrix fields.

**ACCEPT-CMD**:

```bash
uv run pytest tests/test_ablation.py -q
uv run python -m aibench ablation --matrix configs/runs/ablation-matrix.mock.yaml --output-root /tmp/aibench-ablation-test
test -f /tmp/aibench-ablation-test/ablation_*/ablation_report.md || ls /tmp/aibench-ablation-test
./scripts/e2e_pipeline.sh --dry-run
uv run pytest tests/ -q
```

## 3. Engineering Constraints Index

- Surgical changes only to deliverables above.
- Commits: `feat(phaseN):` / `fix(phaseN):` without AI tooling mentions.
- Do not commit `.env`.
- Reuse `report.render_summary_tables_json`, `run_benchmark`, `validate_case_set`.

## 4. Data and Fixture Dependencies

- `tests/fixtures/sessions_min.json` — 2 sessions (1 coding, 1 noise) for filter/generate.
- Existing `seed-v0` for run/ablation mock.
- Optional live DB/API via `.env` (not required for dry-run).

## 5. Regression Protection

- Prior: `tests/test_cases.py`, `test_runner.py`, `test_workspace.py`, `test_extract.py`, `test_history_parse.py`, `test_grading.py` must stay green.
