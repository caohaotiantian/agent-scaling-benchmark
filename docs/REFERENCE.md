# AI-Coding-Assist Benchmark — 项目参考手册（Reference）

| 项 | 值 |
|----|-----|
| 项目 | `aibench` / agent-scaling-benchmark |
| 版本 | 与 `pyproject.toml` 中 `version` 一致（当前 0.1.x） |
| 读者 | 使用者、评测工程师、对接 Agentic Scaling 汇报的同学 |
| 配套演示页 | 仓库根目录 `aibench-project-overview.html` |
| 结果表设计源 | `agentic-scaling-benchmark.html`、`agentic_scaling_benchmark_tables.md` |

本文是 **可执行系统** 的权威参考：概念、架构、配置、CLI、产物、与设计表的映射、操作规程。快速上手见 `USER_GUIDE.md`；设计决策见 `docs/design/*`。

---

## 1. 项目定位与目标

### 1.1 要解决什么问题

Agentic Scaling 需要在统一 Benchmark 协议下比较不同 **算法 / Agent / 模型** 的质量、成本与耗时。本仓库实现领域 Benchmark **「AI 辅助编程」**（内部名 `AI-Coding-Assist`）：

1. 从真实 LLM 会话库（`llm_chat_records`）**抽取 → 筛选 → 生成** 可机评 case；  
2. 在固定 case 集上 **替换 Agent 与模型** 跑测；  
3. 产出对齐 **项目效果综述表 / 通用结果总表** 的机器与人类可读结果。

### 1.2 明确不做

| 不做 | 原因 |
|------|------|
| 自动无人审核发布正式测评集 | 合规与效度风险；`auto-v0` 仅为候选 |
| Fusion / 投机算法本体 | 本仓库是评测 harness，算法以可替换 Agent 接入 |
| 完整 IDE UI 仿真 | workspace + 终端/工具 Agent 足够 |
| 不设 limit 的全库无限扫描 | 始终有默认上限 |

### 1.3 核心概念

| 术语 | 定义 |
|------|------|
| **Case** | 一条可独立评测任务：prompt + workspace + grader |
| **Case set** | 一组 case 的目录名，如 `auto-v0`、`prod-v0` |
| **Draft** | 会话抽取草稿，未必可跑 |
| **Run** | 一次实验：固定 case set + 一种 Agent/模型/算法配置 |
| **消融（Ablation）** | 同一 case set 上跑多组配置并横向对比（非删代码） |
| **主指标** | 默认 `task_success_rate` = 成功数 / 有效 case 数 |
| **有效 case** | 排除 `infra_error` 后参与计分的 case |

---

## 2. 系统架构

```text
┌─────────────────────────────────────────────────────────────┐
│  数据层   MySQL llm_chat_records  /  JSON export             │
└───────────────────────────┬─────────────────────────────────┘
                            ▼
┌─────────────────────────────────────────────────────────────┐
│  构建层   extract → filter → generate → audit / secrets      │
│           产出 benchmarks/ai_coding/cases/auto-v0            │
└───────────────────────────┬─────────────────────────────────┘
                            ▼
┌─────────────────────────────────────────────────────────────┐
│  执行层   materialize workspace → Agent.run → grade          │
│           并行：case_workers / generate workers / ablation   │
└───────────────────────────┬─────────────────────────────────┘
                            ▼
┌─────────────────────────────────────────────────────────────┐
│  报告层   summary.json / tables.json / report.md             │
│           ablation_report.md（多行综述表）                    │
└─────────────────────────────────────────────────────────────┘
```

| 包/目录 | 职责 |
|---------|------|
| `src/aibench/extract/` | 抽库、筛选、生成、软过滤、snapshot 骨架 |
| `src/aibench/agents/` | mock / openai_compat / tool_loop / shell |
| `src/aibench/runner.py` | 单次 run、case 并行、manifest |
| `src/aibench/grading.py` | script / gold / llm_judge / composite |
| `src/aibench/validity.py` | stub-fail、污染、难度、指纹 |
| `src/aibench/ablation.py` | 矩阵消融、基线收益、弱 grader 剔除 |
| `src/aibench/report.py` | 汇总、Wilson CI、分层、失败诊断 |
| `configs/` | **生产配置**（无 mock） |
| `tests/fixtures/` | 单测与 dry-run 用 mock/fixture |

---

## 3. 端到端数据流

### 3.1 仅生成测试用例（不做消融）

```bash
set -a && source .env && set +a

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
uv run python -m aibench audit-cases --case-set auto-v0 --annotate
```

**停在 `auto-v0/`，不要跑 `e2e_pipeline.sh` 全流程（其末尾含 ablation）。**

### 3.2 完整生产流水线（含消融）

```bash
./scripts/e2e_pipeline.sh --limit 100 --max-cases 8
```

### 3.3 `--limit` 与 `--max-cases`

| 参数 | 默认（CLI） | 默认（e2e 脚本） | 含义 |
|------|-------------|------------------|------|
| `--limit` | extract-from-db: **300** | **100** | SQL 最多扫描多少行会话 |
| `--max-cases`（抽库） | **30** | `max-cases×3` | 最多写出多少草稿 |
| `--max-cases`（生成） | **50** | **8** | 最多写出多少最终 case |

不传参则用默认，**不会全库无限扫**。`limit` 控制读库预算，`max-cases` 控制写出预算。

---

## 4. 环境变量参考

| 变量 | 默认 | 必填场景 | 说明 |
|------|------|----------|------|
| `AIBENCH_DB_URL` | 无 | 抽库 | `mysql+pymysql://user:pass@host:3306/db?charset=utf8mb4` |
| `OPENAI_API_KEY` | 无 | 真 Agent / LLM 生成 | 兼容 `AIBENCH_API_KEY` |
| `OPENAI_BASE_URL` | 无 | 同上 | 兼容 API 根，通常含 `/v1` |
| `OPENAI_MODEL` | 无 | 建议设 | 覆盖 model YAML 中的 model 名 |
| `AIBENCH_RETRY_MAX` | `3` | 可选 | HTTP/DB 最大尝试次数 |
| `AIBENCH_RETRY_BACKOFF` | `1.0` | 可选 | 退避基数（秒） |
| `AIBENCH_RETRY_BACKOFF_MAX` | `20` | 可选 | 退避上限 |
| `AIBENCH_CASE_RETRY` | `2` | 可选 | case 因 infra_error 整 case 重跑次数 |
| `AIBENCH_USD_PER_MTOK` | 无 | 可选 | 统一 $/M tokens 估算成本 |
| `AIBENCH_USD_PER_MTOK_INPUT` / `_OUTPUT` | 0.5 / 1.5 | 可选 | 分 input/output 时用均值估算 |

加载：CLI 启动读项目根 `.env`；也可 `set -a && source .env && set +a`。

---

## 5. 生产配置目录（`configs/`，无 mock）

详见 `configs/README.md`。

### 5.1 Agents

| 文件 | adapter | 用途 |
|------|---------|------|
| `agents/openai_compat.yaml` | openai_compat | 单轮 JSON 写文件（默认） |
| `agents/tool_loop.yaml` | tool_loop | 多步 list/read/write/bash/submit |
| `agents/shell.yaml` | shell | 外部 CLI；须配置 `command_template` |

Mock 仅在 `tests/fixtures/configs/`。

### 5.2 Models

| 文件 | 说明 |
|------|------|
| `models/glm52.yaml` | 默认 GLM-5.2；base_url/key 来自环境 |
| `models/glm51.yaml` | 可选对照模型 |
| `models/qwen37.yaml` | 可选对照模型 |

### 5.3 Runs

| 文件 | 说明 |
|------|------|
| `runs/baseline.yaml` | 单次：openai_compat + glm52，`case_workers: 4` |
| `runs/baseline-tool-loop.yaml` | 单次：tool_loop + glm52 |
| `runs/ablation-matrix.yaml` | 消融：openai_compat vs tool_loop |

Run YAML 主要字段：`experiment_name`、`algorithm_name`、`max_steps`、`max_wall_time_s`、`case_set`、`agent_config`、`model_config`、`case_workers`。

消融矩阵字段：`case_set`、`baseline_experiment`、`parallel`、`runs[]`（每项含 agent/model/run_id/run_config）。

---

## 6. CLI 命令参考

入口：`uv run python -m aibench <cmd>` 或 `aibench`。

### 6.1 构建类

| 命令 | 作用 | 关键参数与默认 |
|------|------|----------------|
| `extract-from-db` | 从 MySQL 抽草稿 | `--limit 300`，`--max-cases 30`，`--min-messages 3`，`--max-messages 60`，默认仅 opencode，`--require-gold` 可选 |
| `extract-cases` | 从 JSON 抽草稿 | `--max-cases 50` |
| `filter-drafts` | 规则筛选 | `--llm-soft` 二次 LLM 过滤 |
| `generate-cases` | 草稿→case | 默认 LLM；`--heuristic-only`；`--max-cases 50`；`--workers 1`；`--audit`；`--secrets-scan` |
| `validate-cases` | Schema 校验 | 默认 case-set `auto-v0` |
| `audit-cases` | 科学效度审计 | `--annotate`，`--fail-on-error` |
| `secrets-scan` | 密钥扫描 | `--case-set` 或 `--input-dir` |
| `snapshot-skeleton` | 从 files 打 snapshot 目录 | `--case-set` |
| `promote` | 人工门控发布 | `--from-set auto-v0 --to-set prod-v0`，`--require-audit`，`--case-id` |

### 6.2 执行类

| 命令 | 作用 | 关键参数 |
|------|------|----------|
| `run` | 单次实验 | `--run-config` 默认 `baseline.yaml`，`--workers` case 并行 |
| `ablation` | 矩阵消融 | `--matrix`，`--parallel`，`--baseline-experiment`，默认剔除 weak_grader，`--export-csv/xlsx` |
| `check-summary` | 检查 summary 必填键 | `run_dir` |
| `export-ablation` | 从消融目录导出表 | `--ablation-dir` |

### 6.3 脚本

| 脚本 | 默认 | 说明 |
|------|------|------|
| `scripts/run_benchmark.sh` | openai_compat + glm52 + auto-v0 | 生产单次跑测 |
| `scripts/e2e_pipeline.sh` | limit=100, max-cases=8, 生产矩阵 | 全流程；末尾消融 |
| `scripts/lint.sh` | ruff check/format | 代码风格 |
| `scripts/install-hooks.sh` | pre-commit | 提交前 ruff |

---

## 7. Case 协议摘要

Schema：`benchmarks/ai_coding/schemas/case.schema.json`。

必填：`case_id`、`schema_version`、`task_type`、`language`、`prompt`、`context.files`、`grader`。

| grader.mode | 行为 |
|-------------|------|
| `script` | 执行 `command`（白名单：pytest/python）exit 0 为成功 |
| `gold` | 文件/key_lines 匹配 |
| `llm_judge` | 模型打分 ≥ threshold |
| `composite` | script 优先再 gold |

`context.workspace`：`inline` | `snapshot` | `git` | `mixed` + setup_commands。

元数据常用：`source`、`source_session_id`、`generation`（llm/heuristic）、`review_status`、`weak_grader`、`difficulty`、`fingerprint`、`validity_ok`。

### 7.1 科学效度门禁（audit）

| 检查 | 说明 |
|------|------|
| stub_fail | script 初始 workspace 必须测不过 |
| contamination | 正解不得已在初始上下文 |
| difficulty | easy/medium/hard 启发式 |
| fingerprint / set fingerprint | 去重与可复现 |

---

## 8. 运行产物与使用方式

### 8.1 单次 Run 目录

`runs/<Benchmark>__<timestamp>_<run_id>/`

| 文件 | 用途 |
|------|------|
| `run_manifest.json` | 配置与 case_set_fingerprint |
| `summary.json` | 聚合指标、CI、分层、失败诊断 |
| `tables.json` | `overview_row` + `general_row`（对齐结果表） |
| `results.jsonl` | 每 case 一行 |
| `report.md` | 人类可读 |
| `cases/<id>/` | workspace 与明细 |

### 8.2 消融目录

`runs/ablation_<timestamp>/`

| 文件 | 用途 |
|------|------|
| `ablation_report.md` | **多行综述对比表**（汇报首选） |
| `ablation_summary.json` | 机器汇总 |
| `ablation_overview.csv` | 可选 |
| 子 run 目录 | 各组完整结果 |

### 8.3 如何使用输出

| 目的 | 看什么 |
|------|--------|
| 汇报 / 选型 | `ablation_report.md` 或 `tables.json.overview_row` |
| 归因 | `report.md` 失败诊断、`results.jsonl` |
| 复现 | manifest + case set fingerprint |
| 入库 / 看板 | 读 JSON/CSV，按列名映射到总表 |

---

## 9. 与初始设计文件的关系

| 设计文件 | 角色 |
|----------|------|
| `agentic-scaling-benchmark.html` | 结果表与评测协议的 **设计报告**（统一粒度、综述表、通用表、落盘建议） |
| `agentic_scaling_benchmark_tables.md` | **字段字典**（列名、含义、类型） |

本仓库是 **执行与填表实现**：

```text
设计：一行 = 同一 Benchmark + case set + 一个算法配置 + 预算 + 一次 run
实现：aibench run / ablation 产出对应行
主指标：task_success_rate（AI 编程半确定性）
空字段：无数据源时填 null，不编造 Fusion 专用列
```

| 设计表 | 本项目映射 |
|--------|------------|
| 项目效果综述表 | `overview_row` / ablation 多行 MD 表 |
| 通用结果总表 | `summary.json` 核心字段 + `general_row` |
| 落盘 runs/... | runner 目录结构 |

---

## 10. 消融（Ablation）定义

**消融** = 固定测评集，只改变 Agent/模型（及算法标签），跑多组对照并汇总。  
**不是** 删除模型组件做学术消融的狭义实现，而是 **对照实验矩阵**。

当前生产矩阵：`openai_compat+GLM-5.2` vs `tool_loop+GLM-5.2`。

---

## 11. 重试与并行

| 机制 | 配置 | 作用 |
|------|------|------|
| HTTP/DB 重试 | `AIBENCH_RETRY_MAX` 等 | 超时、连接、空 content |
| Case infra 重试 | `AIBENCH_CASE_RETRY` | materialize/agent 基础设施失败 |
| Case 并行 | `run --workers` / `case_workers` | 单次 run 内并行 |
| 生成并行 | `generate-cases --workers` | LLM 生成并行 |
| 消融并行 | `ablation --parallel` | 多实验行并行 |

---

## 12. 代码质量

```bash
./scripts/install-hooks.sh   # pre-commit: ruff format + import + lint
./scripts/lint.sh
```

CI：`.github/workflows/ci.yml`（ruff + pytest + dry-run）。

---

## 13. 目录速查

```text
agent-scaling-benchmark/
├── aibench-project-overview.html   # 演示 / 介绍页
├── agentic-scaling-benchmark.html  # 结果表设计报告
├── agentic_scaling_benchmark_tables.md
├── configs/                        # 生产配置
├── docs/REFERENCE.md               # 本文
├── docs/USER_GUIDE.md              # 操作向导
├── benchmarks/ai_coding/cases/     # auto-v0 等 case 集
├── scripts/
├── src/aibench/
└── runs/                           # 实验结果（本地生成，gitignore）
```

---

## 14. 常见问题

**Q: 只生成 case？**  
只跑 extract → filter → generate → validate/audit，不要 `e2e_pipeline` 全流程。

**Q: auto-v0 能直接当正式集？**  
否。须人工审 + `promote --require-audit` 到 `prod-v0`。

**Q: 不设 limit/max-cases？**  
用默认值，不会全库无限跑（见 §3.3）。

**Q: 结果表很多空列？**  
正常。仅填充有数据的质量/成本/时间/Agent 字段。

---

*文档随代码演进；以仓库 CLI `--help` 与 `configs/` 现行内容为准。*
