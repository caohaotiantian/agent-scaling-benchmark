# Production configs (no mock)

All files under `configs/` are for real evaluation. Mock configs live only in
`tests/fixtures/configs/` for unit tests and `--dry-run`.

## Layout

| Path | Purpose |
|------|---------|
| `agents/openai_compat.yaml` | Single-turn Chat Completions agent (default) |
| `agents/tool_loop.yaml` | Multi-step list/read/write/bash agent |
| `agents/shell.yaml` | External CLI wrapper (set `command_template`) |
| `models/glm52.yaml` | GLM-5.2 (default; URL/key from `.env`) |
| `models/glm51.yaml` | GLM-5.1 optional ablation slot |
| `models/qwen37.yaml` | Qwen3.7-Plus optional slot |
| `runs/baseline.yaml` | Default single run (openai_compat + glm52) |
| `runs/baseline-tool-loop.yaml` | Single run with tool_loop |
| `runs/ablation-matrix.yaml` | Production ablation (openai_compat vs tool_loop) |

## Required environment

```bash
# .env
AIBENCH_DB_URL=mysql+pymysql://...
OPENAI_API_KEY=...
OPENAI_BASE_URL=http://host:port/v1
OPENAI_MODEL=GLM-5.2   # optional override of yaml model field
```

## Commands

```bash
# Single production run
./scripts/run_benchmark.sh
# or
uv run python -m aibench run --run-config configs/runs/baseline.yaml

# Full DB → cases → ablation
./scripts/e2e_pipeline.sh --limit 100 --max-cases 8

# Ablation only
uv run python -m aibench ablation --matrix configs/runs/ablation-matrix.yaml
```
