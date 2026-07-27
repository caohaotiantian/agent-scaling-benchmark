# Production configs（无 mock）

> **展示版（推荐）**：[docs/html/configs.html](../docs/html/configs.html)

`configs/` 下全部文件用于**真实评测**。Mock 仅存在于 `tests/fixtures/configs/`（单测与 `e2e_pipeline.sh --dry-run`）。

权威字段说明见 [参考手册 HTML](../docs/html/reference.html) §7（Markdown 源：`docs/REFERENCE.md`）。

## Layout

| Path | Purpose |
|------|---------|
| `agents/openai_compat.yaml` | 单轮 Chat Completions 写文件（**默认**） |
| `agents/tool_loop.yaml` | 多步 list/read/write/bash/submit |
| `agents/shell.yaml` | 外部 CLI 包装（须配置 `command_template`） |
| `models/glm52.yaml` | GLM-5.2（默认；URL/key 来自 `.env`） |
| `models/glm51.yaml` | 可选对照 |
| `models/qwen37.yaml` | 可选对照 |
| `runs/baseline.yaml` | 单次：openai_compat + glm52，`case_workers: 4` |
| `runs/baseline-tool-loop.yaml` | 单次：tool_loop + glm52 |
| `runs/ablation-matrix.yaml` | 消融：openai_compat vs tool_loop |

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
uv run python -m aibench ablation \
  --matrix configs/runs/ablation-matrix.yaml \
  --baseline-experiment openai-compat-glm52 \
  --export-csv
```

## Notes

- 模型 YAML 中 `base_url: null` 表示使用环境变量中的网关地址。
- 消融矩阵可通过注释块启用第二模型行。
- `shell.yaml` 的 `command_template` 默认为空，接入外部 CLI 前必须填写。
