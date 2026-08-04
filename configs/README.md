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
| `runs/ablation-matrix.yaml` | 消融：基线 / 模型轴（glm51）/ Agent 轴（tool_loop），每次只变一条轴 |
| `runs/anchor-panel.yaml` | 校准锚点面板：弱/中/强，供 `calibrate-cases` 实测用例区分度 |

## Required environment

```bash
# .env
AIBENCH_DB_URL=mysql+pymysql://...
OPENAI_API_KEY=...
OPENAI_BASE_URL=http://host:port/v1
OPENAI_MODEL=GLM-5.2   # 仅当模型 YAML 的 `model` 为空时作为兜底
```

**模型解析优先级：模型 YAML 的 `model` 字段优先，`OPENAI_MODEL` 只在其为空时兜底。**
（与 `base_url` 一致。反过来的话，多模型消融的每一行都会静默跑成 `OPENAI_MODEL` 指定的
同一个模型，而报告仍按不同模型标注 —— 那是会得出错误结论的静默失败。）

## Commands

```bash
# Single production run
./scripts/run_benchmark.sh
# or
uv run python -m aibench run --run-config configs/runs/baseline.yaml

# Full DB → cases → ablation
./scripts/e2e_pipeline.sh --limit 100 --max-cases 8

# 同上，并加入经验校准与按区分度选题（成本 = 锚点数 × repeats 次全量跑测）
./scripts/e2e_pipeline.sh --limit 100 --max-cases 8 --calibrate --repeats 3

# 并行：对网关的实际并发 ≈ parallel × case_workers，本网关实测 16 并发仍线性
uv run python -m aibench calibrate-cases --case-set auto-v0 --repeats 2 \
  --parallel 3 --workers 5

# Ablation only
uv run python -m aibench ablation \
  --matrix configs/runs/ablation-matrix.yaml \
  --baseline-experiment openai-compat-glm52 \
  --export-csv
```

## Notes

- 模型 YAML 中 `base_url: null` 表示使用环境变量中的网关地址。
- 消融矩阵每行只改**一条轴**（模型或 Agent），否则 McNemar 配对检验的差异无法归因。
- 锚点面板必须**同时跨越模型与 Agent 两条轴**，且含一个明显弱的锚点；否则 `spread` 恒为 0，
  校准会把所有用例误判为无区分度。
- `shell.yaml` 的 `command_template` 默认为空，接入外部 CLI 前必须填写。
