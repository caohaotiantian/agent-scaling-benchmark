# AI 辅助编程 Benchmark（Agentic Scaling）

面向 **Agentic Scaling** 的「AI 辅助编程」领域 Benchmark：统一 case 协议、可替换 Agent/模型、一键跑测，并产出对齐项目结果表的实验归档。

| 文档 | 说明 |
|------|------|
| **[用户使用手册](docs/USER_GUIDE.md)** | **配置项、默认值、CLI/脚本参数、快速上手** |
| [E2E 设计](docs/design/2026-07-25-e2e-session-to-benchmark.md) | 会话→case→消融闭环 |
| [Benchmark 协议设计](docs/design/2026-07-25-ai-coding-benchmark.md) | 任务定义、判分、指标 |
| [结果表字段](agentic_scaling_benchmark_tables.md) | 综述表 / 通用结果总表 |
| [结果表设计报告](agentic-scaling-benchmark.html) | 统一评测协议与落盘结构 |

## 快速开始

```bash
# 依赖（推荐 uv）
uv sync --extra dev

# 安装 git pre-commit（ruff：format + import 整理 + lint）
./scripts/install-hooks.sh
# 手动: ./scripts/lint.sh

# 生产默认：openai_compat + GLM-5.2 + auto-v0（需 .env 与 case 集）
./scripts/run_benchmark.sh

# 离线 smoke（仅 tests/fixtures，非正式生产配置）
./scripts/e2e_pipeline.sh --dry-run
```

生产配置说明见 [`configs/README.md`](configs/README.md)（**不含 mock**）。

### 全流程（会话 → 候选 case → 消融）

```bash
set -a && source .env && set +a   # AIBENCH_DB_URL / OPENAI_*

# 生产：DB → LLM 生成 auto-v0 → openai_compat vs tool_loop 消融
./scripts/e2e_pipeline.sh --limit 100 --max-cases 8

# 或分步：
uv run python -m aibench extract-from-db --output-dir benchmarks/ai_coding/cases/drafts-from-db --limit 100 --require-gold
uv run python -m aibench filter-drafts --input-dir benchmarks/ai_coding/cases/drafts-from-db --output-dir benchmarks/ai_coding/cases/drafts-kept
uv run python -m aibench generate-cases --input-dir benchmarks/ai_coding/cases/drafts-kept --output-dir benchmarks/ai_coding/cases/auto-v0 --audit
uv run python -m aibench ablation --matrix configs/runs/ablation-matrix.yaml --case-set auto-v0
```

`auto-v0` 是**自动候选集**（`needs_review`），不是 published 正式集。消融对比 Agent/模型请改矩阵 YAML。

成功后在 `runs/AI-Coding-Assist__*/` 下生成：

- `run_manifest.json` — 实验标识与配置
- `summary.json` — 聚合指标（表数据源）
- `tables.json` — 综述表行 + 通用表关键字段
- `results.jsonl` — 每 case 一行
- `report.md` — 人类可读报告
- `cases/<case_id>/` — workspace 与明细

## 替换 Agent / 模型

```bash
# 使用 OpenAI-compatible API（需设置 AIBENCH_API_KEY 或 OPENAI_API_KEY）
export AIBENCH_API_KEY=sk-...
./scripts/run_benchmark.sh \
  --agent configs/agents/openai_compat.yaml \
  --model configs/models/openai-compat.example.yaml
```

或直接调用模块：

```bash
uv run python -m aibench run \
  --run-config configs/runs/seed-baseline.yaml \
  --agent configs/agents/mock.yaml \
  --model configs/models/mock-model.yaml \
  --case-set seed-v0
```

自定义 Agent：实现 `AgentAdapter` 并在 `aibench.agents.registry` 注册，或提交适配器配置 `adapter: your_name`。

## Case 集

- 目录：`benchmarks/ai_coding/cases/<case_set>/*.json`
- Schema：`benchmarks/ai_coding/schemas/case.schema.json`
- 校验：`uv run python -m aibench validate-cases --case-set seed-v0`

### 现场还原（文件依赖）

测评时不访问用户原机器；依赖必须通过 case 声明的通道还原：

| 层级 | 方式 | 配置 |
|------|------|------|
| L1 | JSON 内嵌文件 | `context.files` |
| L2 | 目录/tar/zip 快照 | `context.workspace.snapshot.path` |
| L3 | Git 固定 commit | `context.workspace.git.{url,ref,subdir}` |
| L4 | 安装/环境 | `setup_commands` / `env` |
| mixed | 快照/git 打底 + files 覆盖 | `mode: mixed` |

详见 [docs/design/workspace-restoration.md](docs/design/workspace-restoration.md)。  
示例 case：`seed-v0-004-snapshot-div`（`snapshots/calc_project` + inline 测试覆盖）。

## 从真实会话生成 Case 草稿

### A. 直接从 MySQL `llm_chat_records` 抽取（推荐）

表字段：`request_id`, `start_time`, `model`, `requests_tags`, `tools`, `full_history`, `created_at`, `key_alias`。

凭据**只通过环境变量**注入，不要写进仓库：

```bash
export AIBENCH_DB_URL='mysql+pymysql://USER:PASS@HOST:3306/opencsitool_db?charset=utf8mb4'

uv run python -m aibench extract-from-db \
  --output-dir benchmarks/ai_coding/cases/drafts-from-db \
  --limit 400 \
  --max-cases 30 \
  --require-gold \
  --export-raw /tmp/aibench_draft_meta.json
```

常用选项：

| 选项 | 含义 |
|------|------|
| 默认 | 只扫 `User-Agent` 含 `opencode` 的请求 |
| `--all-agents` | 不限制 opencode |
| `--require-gold` | 只要能从 assistant 抽出代码块的草稿 |
| `--min-messages` / `--max-messages` | 控制 `JSON_LENGTH(full_history)`，避免超长轨迹 |
| `--since` / `--until` | 时间窗 `YYYY-MM-DD` |

### B. 从规范化 JSON 导出抽取

```bash
uv run python -m aibench extract-cases \
  --input path/to/sessions_export.json \
  --output-dir benchmarks/ai_coding/cases/drafts-from-db
```

规范化导出示例：

```json
{
  "sessions": [
    {
      "session_id": "s1",
      "messages": [
        {"role": "user", "content": "请修复 average 空列表崩溃"},
        {"role": "assistant", "content": "..."}
      ],
      "artifacts": [
        {"type": "file", "path": "stats.py", "content": "def average..."},
        {"type": "accepted_file", "path": "stats.py", "content": "...", "accepted": true}
      ],
      "outcome": {"user_accepted": true}
    }
  ]
}
```

草稿 **必须人工审核与脱敏** 后再作为正式 case set（不要直接把 `drafts-from-db` 当发布集）。

## 设计摘要

| 项 | 选择 |
|----|------|
| Benchmark 名 | `AI-Coding-Assist` |
| 主范式 | 半确定性（script / gold / 预留 llm_judge） |
| 主指标 | `task_success_rate` |
| 一行结果语义 | 同一 Benchmark + case set + 算法配置 + 预算档 + 一次 run |

## 测试

```bash
uv run pytest tests/ -q
```

## 目录结构

```text
benchmarks/ai_coding/     # case 与 schema
configs/{agents,models,runs}/
src/aibench/              # harness
scripts/run_benchmark.sh  # 一键入口
runs/                     # 实验结果（本地生成）
docs/design/              # Phase A 设计
```
