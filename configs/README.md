# Production configs（无 mock）

> **展示版（推荐）**：[用户手册](../docs/html/manual.html)

`configs/` 下全部文件用于**真实评测**。Mock 仅存在于 `tests/fixtures/configs/`（单测与 `e2e_pipeline.sh --dry-run`）。

权威字段说明见 [`docs/REFERENCE.md`](../docs/REFERENCE.md) §7。（[参考资料 HTML](../docs/html/reference.html) 是另一份文档，讲设计论证，不含配置字段。）

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
| `runs/anchor-panel.yaml` | 校准锚点面板（T1–T3）：弱/中/强单轮与多步混合 |
| `runs/anchor-panel-retrieval.yaml` | 检索层（T4+）面板：**全部为多步 agent**，单轮无法施展 A2 |
| `agents/tool_loop_frugal.yaml` | 受限多步 agent：短步数预算、无 shell，作检索层地板锚 |

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
# 注意：消融矩阵里的 `parallel:` 直到 2026-08-17 才真正生效 —— 此前 CLI 的默认值 1 会短路掉它，
# 所以**只写 YAML 而不在命令行传 `--parallel` 的人拿到的是串行**。已落盘的实验不是这一类：
# 16 份 `ablation_summary.json` 里 13 份记着 `parallel: 3`（这 16 份不含 `runs/e2e-dry-run/` 下 CI 自己跑出来的那些；连它们一起数是 48 份），且每一份都有 3 个 run 目录共用同一
# 秒级时间戳 —— 它们确实是三路并发跑的，因为命令行显式传了 `--parallel 3`。
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

## 本文未逐条展开的配置

上面的布局表按目录讲结构，这里按文件补齐未逐条展开的部分。
（**不在这里数文件**：逐个文件的完整清单在 `docs/REFERENCE.md` §7.1，那张表由目录列表生成、有门禁核对。
本文件此前写「共 23 个 YAML」，实际 40 个。）

| 文件 | 用途 |
|---|---|
| `agents/bare_model.yaml` | **无脚手架口径** —— 比较模型时唯一该用的适配器（`docs/HANDOFF.md` §0.4） |
| `models/glm5.yaml` / `deepseek-v4-flash.yaml` / `glm52-sampling.yaml` | 模型消融与采样实验用 |
| `runs/ablation-bare-models.yaml` | 用 `bare_model` 重跑模型消融（§0.6 的第 2 优先级） |
| `runs/ablation-models.yaml` / `ablation-models-toolloop.yaml` / `ablation-two-models.yaml` | 模型轴消融矩阵 |
| `runs/baseline-bare.yaml` / `baseline-tool-loop-frugal.yaml` | 对应适配器的基线 run |
| `runs/passk.yaml` | 采样扩展（pass@k）。**已**配好 `models/glm52-sampling.yaml`；注意 temperature 0 下 pass@k 恒等于 pass@1 |
