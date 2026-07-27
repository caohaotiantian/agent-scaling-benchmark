# AI-Coding-Assist Benchmark（Agentic Scaling）

从真实 AI 编程会话构建可机评用例，在固定 case 集上替换 **Agent / 模型** 跑测，并产出对齐项目 **结果表** 口径的实验报告。

---

## 文档入口（按细致程度）

| 文档 | 说明 |
|------|------|
| **[项目介绍演示页](aibench-project-overview.html)** | 浏览器打开：目标、架构、命令、产物、与设计表关系 |
| **[参考手册 REFERENCE](docs/REFERENCE.md)** | **参数级权威参考**（CLI / 配置 / 环境变量 / 映射） |
| [用户向导 USER_GUIDE](docs/USER_GUIDE.md) | 操作步骤与配置速查 |
| [生产配置说明](configs/README.md) | `configs/` 生产配置（**无 mock**） |
| [结果表设计报告](agentic-scaling-benchmark.html) | 协议与表结构源头 |
| [结果表字段字典](agentic_scaling_benchmark_tables.md) | 综述表 / 通用总表列定义 |

---

## 快速开始

```bash
uv sync --extra dev
./scripts/install-hooks.sh    # ruff format + import + lint

cp .env.example .env          # 填写 AIBENCH_DB_URL / OPENAI_*
set -a && source .env && set +a
```

### 只要生成测试用例（不做消融）

```bash
uv run python -m aibench extract-from-db \
  --output-dir benchmarks/ai_coding/cases/drafts-from-db \
  --limit 100 --max-cases 30 --require-gold

uv run python -m aibench filter-drafts \
  --input-dir benchmarks/ai_coding/cases/drafts-from-db \
  --output-dir benchmarks/ai_coding/cases/drafts-kept

uv run python -m aibench generate-cases \
  --input-dir benchmarks/ai_coding/cases/drafts-kept \
  --output-dir benchmarks/ai_coding/cases/auto-v0 \
  --max-cases 8 --audit --secrets-scan

uv run python -m aibench validate-cases --case-set auto-v0
```

产物：`benchmarks/ai_coding/cases/auto-v0/`。

### 完整生产流水线（含消融对比）

```bash
./scripts/e2e_pipeline.sh --limit 100 --max-cases 8
```

### 单次跑测（已有 auto-v0）

```bash
./scripts/run_benchmark.sh
# 默认：openai_compat + GLM-5.2 + auto-v0
```

### 离线冒烟（非生产配置）

```bash
./scripts/e2e_pipeline.sh --dry-run
```

---

## 项目在做什么

```text
MySQL 会话 → 草稿 → 筛选 → 可跑 Case（auto-v0）
                              ↓
              Agent/模型配置（可替换）→ 判分
                              ↓
         summary / tables / ablation_report
         （对齐综述表与通用结果总表）
```

- **消融**：同一 case 集上对比多组 Agent/模型，不是删网络层。  
- **生产配置**：见 `configs/`（无 mock；mock 仅在 `tests/fixtures`）。  
- **与设计表**：`tables.json` / `ablation_report.md` 填 `agentic-scaling-benchmark.html` 与 `agentic_scaling_benchmark_tables.md` 定义的列。

---

## 目录结构（摘要）

```text
configs/                 生产 Agent / 模型 / Run / 消融矩阵
docs/REFERENCE.md        参考手册
aibench-project-overview.html   演示介绍页
src/aibench/             可执行 harness
benchmarks/ai_coding/    case schema 与 case 集
scripts/                 一键脚本与 lint/hooks
runs/                    实验结果（本地生成）
```

---

## 开发

```bash
./scripts/lint.sh
uv run pytest tests/ -q
```
