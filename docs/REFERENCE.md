# AI-Coding-Assist Benchmark — 项目参考手册（Reference）

> **展示版（推荐）**：[docs/html/reference.html](html/reference.html)  
> 文档站首页：[docs/html/index.html](html/index.html) · 项目介绍：[docs/html/project-overview.html](html/project-overview.html)

| 项 | 值 |
|----|-----|
| 项目 | `aibench` / agent-scaling-benchmark |
| 版本 | 与 `pyproject.toml` 中 `version` 一致（当前 **0.1.0**） |
| 入口 | `uv run python -m aibench` · 安装后 `aibench` |
| 读者 | 使用者、评测工程师、对接 Agentic Scaling 汇报的同学 |
| 演示页 | [`docs/html/project-overview.html`](html/project-overview.html) |
| 结果表设计源 | [`docs/html/agentic-scaling-benchmark.html`](html/agentic-scaling-benchmark.html)、[`docs/html/tables.html`](html/tables.html)（字段字典源 `_src/tables.md`） |
| 本文 HTML | [`docs/html/reference.html`](html/reference.html) |

本文是 **可执行系统** 的权威参考：概念、架构、Case 协议、配置、CLI、脚本、产物、设计表映射、操作规程与故障排查。  
快速上手见 [用户向导 HTML](html/user-guide.html)（源：`USER_GUIDE.md`）；设计决策见 `docs/design/*`。运行时以 `aibench <cmd> -h` 与仓库现行 `configs/` 为准。  
重建 HTML：`uv run python scripts/build_docs_html.py`

---

## 目录

1. [项目定位与目标](#1-项目定位与目标)
2. [核心概念与术语表](#2-核心概念与术语表)
3. [系统架构](#3-系统架构)
4. [端到端数据流与操作规程](#4-端到端数据流与操作规程)
5. [环境与依赖](#5-环境与依赖)
6. [环境变量完整参考](#6-环境变量完整参考)
7. [生产配置体系 `configs/`](#7-生产配置体系-configs)
8. [CLI 命令完整参考](#8-cli-命令完整参考)
9. [一键脚本参考](#9-一键脚本参考)
10. [Case 协议与 Schema](#10-case-协议与-schema)
11. [Workspace 物化规则](#11-workspace-物化规则)
12. [Agent 适配器](#12-agent-适配器)
13. [判分（Grading）](#13-判分grading)
14. [科学效度审计与发布门控](#14-科学效度审计与发布门控)
15. [重试与并行](#15-重试与并行)
16. [运行产物与结果表映射](#16-运行产物与结果表映射)
17. [消融（Ablation）](#17-消融ablation)
18. [与初始设计文件的关系](#18-与初始设计文件的关系)
19. [仓库目录结构](#19-仓库目录结构)
20. [代码质量与 CI](#20-代码质量与-ci)
21. [扩展点](#21-扩展点)
22. [常见问题](#22-常见问题)
23. [命令速查卡](#23-命令速查卡)

---

## 1. 项目定位与目标

### 1.1 要解决什么问题

Agentic Scaling 需要在统一 Benchmark 协议下比较不同 **算法 / Agent / 模型** 的质量、成本与耗时。本仓库实现领域 Benchmark **「AI 辅助编程」**（内部名 `AI-Coding-Assist`）：

1. 从真实 LLM 会话库（MySQL 表 `llm_chat_records`）**抽取 → 筛选 → 生成** 可机评 case；
2. 在固定 case 集上 **替换 Agent 与模型** 跑测；
3. 产出对齐 **项目效果综述表 / 通用结果总表** 的机器可读与人类可读结果。

### 1.2 明确不做

| 不做 | 原因 |
|------|------|
| 自动无人审核发布正式测评集 | 合规与效度风险；`auto-v0` 仅为候选 |
| Fusion / 投机算法本体 | 本仓库是评测 harness；算法以可替换 Agent 接入 |
| 完整 IDE UI 仿真 | workspace + 终端/工具 Agent 足够 |
| 不设 limit 的全库无限扫描 | 始终有默认上限（见 §4.3） |
| 编造无数据源的结果表列 | 无数据时填 `null` |

### 1.3 生产默认（无 mock）

| 维度 | 默认 |
|------|------|
| Agent | `configs/agents/openai_compat.yaml` |
| 模型 | `configs/models/glm52.yaml`（`GLM-5.2`） |
| 单次 Run | `configs/runs/baseline.yaml` |
| 消融矩阵 | `configs/runs/ablation-matrix.yaml` |
| Case 集 | `auto-v0` |
| Mock | **仅** `tests/fixtures/configs/` 与 `--dry-run` |

---

## 2. 核心概念与术语表

| 术语 | 定义 |
|------|------|
| **Case** | 一条可独立评测任务：`prompt` + workspace（`context.files` / snapshot / git）+ `grader` |
| **Case set** | 一组 case 的目录名；解析路径见 §8.0。例：`auto-v0`、`prod-v0`、`seed-v0` |
| **Draft** | 从会话抽出的草稿 JSON，未必 schema 合法、未必可机评 |
| **auto-v0** | 自动生成的 **候选** case 集（`review_status=needs_review`），**非**正式发布集 |
| **prod-v0** | 人工 `promote` 后的正式集命名约定 |
| **Run** | 一次实验：固定 case set + 一种 Agent/模型/算法配置 + 预算 |
| **消融（Ablation）** | 同一 case set 上跑多组配置并横向对比（对照实验矩阵，非删网络层） |
| **主指标** | `task_success_rate` = 成功数 / **有效 case 数** |
| **有效 case** | 排除 `infra_error=true` 后参与计分的 case |
| **评判类型** | 本领域固定为 **半确定性**（`judgment_type=半确定性`） |
| **weak_grader** | 元数据标记：判分偏弱（如仅 gold 无 script）；消融默认剔除 |
| **case_set_fingerprint** | case 集内容指纹，写入 manifest/summary，用于复现核对 |

---

## 3. 系统架构

```text
┌─────────────────────────────────────────────────────────────┐
│  数据层   MySQL llm_chat_records  /  JSON sessions 导出      │
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
│           重试：HTTP/DB + case infra                         │
└───────────────────────────┬─────────────────────────────────┘
                            ▼
┌─────────────────────────────────────────────────────────────┐
│  报告层   summary.json / tables.json / report.md             │
│           ablation_report.md（多行综述表）+ CSV/XLSX          │
└─────────────────────────────────────────────────────────────┘
```

| 包 / 路径 | 职责 |
|-----------|------|
| `src/aibench/extract/` | 抽库、规则筛选、LLM 软过滤、生成、snapshot 骨架 |
| `src/aibench/agents/` | `mock` / `openai_compat` / `tool_loop` / `shell` |
| `src/aibench/runner.py` | 单次 run、case 并行、manifest |
| `src/aibench/grading.py` | script / gold / llm_judge / composite |
| `src/aibench/workspace.py` | workspace 物化（inline/snapshot/git/mixed） |
| `src/aibench/validity.py` | stub-fail、污染、难度、指纹 |
| `src/aibench/ablation.py` | 矩阵消融、基线收益、弱 grader 剔除 |
| `src/aibench/report.py` | 汇总、Wilson CI、分层、失败诊断、`tables.json` |
| `src/aibench/stats.py` | Wilson 区间、分层统计 |
| `src/aibench/retry.py` | HTTP/DB 重试策略 |
| `src/aibench/promote.py` | 人工发布门控 |
| `src/aibench/secrets_scan.py` | 密钥模式扫描 |
| `src/aibench/cli.py` | 全部子命令 |
| `configs/` | **生产配置**（无 mock） |
| `tests/fixtures/` | 单测与 dry-run 用 mock / fixture |

---

## 4. 端到端数据流与操作规程

### 4.1 仅生成测试用例（不做消融）

```bash
set -a && source .env && set +a

uv run python -m aibench extract-from-db \
  --output-dir benchmarks/ai_coding/cases/drafts-from-db \
  --limit 100 --max-cases 30 --require-gold

uv run python -m aibench filter-drafts \
  --input-dir benchmarks/ai_coding/cases/drafts-from-db \
  --output-dir benchmarks/ai_coding/cases/drafts-kept \
  --dropped-dir benchmarks/ai_coding/cases/drafts-dropped \
  --report benchmarks/ai_coding/filter_report.json

uv run python -m aibench generate-cases \
  --input-dir benchmarks/ai_coding/cases/drafts-kept \
  --output-dir benchmarks/ai_coding/cases/auto-v0 \
  --max-cases 8 --audit --secrets-scan

uv run python -m aibench validate-cases --case-set auto-v0
uv run python -m aibench audit-cases --case-set auto-v0 --annotate
```

**停在 `auto-v0/`。** 不要跑 `e2e_pipeline.sh` 全流程（其末尾会执行 ablation）。

### 4.2 完整生产流水线（含消融）

```bash
./scripts/e2e_pipeline.sh --limit 100 --max-cases 8
```

等价阶段：extract → filter → generate（含 audit/secrets-scan）→ validate → audit → ablation。

### 4.3 `--limit` 与 `--max-cases`

| 参数 | CLI 默认 | e2e 脚本默认 | 含义 |
|------|----------|--------------|------|
| `--limit`（抽库） | **300** | **100** | SQL 最多扫描多少行会话 |
| `--max-cases`（抽库） | **30** | `max-cases×3` | 最多写出多少草稿 |
| `--max-cases`（生成） | **50** | **8** | 最多写出多少最终 case |

不传参则用默认，**不会全库无限扫**。`limit` = 读库预算；`max-cases` = 写出预算。

### 4.4 单次跑测 / 仅消融

```bash
# 已有 auto-v0
./scripts/run_benchmark.sh

# 或显式
uv run python -m aibench run --run-config configs/runs/baseline.yaml

# 仅消融
uv run python -m aibench ablation \
  --matrix configs/runs/ablation-matrix.yaml \
  --case-set auto-v0 \
  --baseline-experiment openai-compat-glm52 \
  --export-csv
```

### 4.5 离线冒烟（非生产）

```bash
./scripts/e2e_pipeline.sh --dry-run
```

使用 `tests/fixtures` 与 mock 矩阵；**不代表生产结果**。

### 4.6 发布正式集

```bash
uv run python -m aibench promote \
  --from-set auto-v0 --to-set prod-v0 \
  --require-audit \
  --case-id <id1> --case-id <id2>
```

---

## 5. 环境与依赖

### 5.1 要求

| 项 | 要求 |
|----|------|
| Python | ≥ 3.11 |
| 包管理 | 推荐 `uv` |
| 可选 | MySQL 可达（抽库）、OpenAI 兼容 API（生成与真 Agent） |

### 5.2 安装

```bash
cd agent-scaling-benchmark
uv sync --extra dev
./scripts/install-hooks.sh   # pre-commit: ruff format + import + lint
cp .env.example .env         # 填写密钥与连接
set -a && source .env && set +a
```

CLI 启动时会尝试加载项目根目录 `.env`。

### 5.3 运行时依赖（摘要）

`httpx`、`pyyaml`、`jsonschema`、`rich`、`pytest`、`pymysql`、`sqlalchemy`、`openpyxl`（XLSX 导出）。  
开发：`ruff`、`pre-commit`。

---

## 6. 环境变量完整参考

| 变量 | 默认 | 必填场景 | 说明 |
|------|------|----------|------|
| `AIBENCH_DB_URL` | 无 | 抽库 | SQLAlchemy URL，例：`mysql+pymysql://user:pass@host:3306/opencsitool_db?charset=utf8mb4` |
| `DATABASE_URL` | 无 | 可选 | `AIBENCH_DB_URL` 未设时的库连接兜底 |
| `OPENAI_API_KEY` | 无 | 真 Agent / LLM 生成 | Bearer；兼容 `AIBENCH_API_KEY` |
| `OPENAI_BASE_URL` | 无 | 同上 | Chat Completions 根，通常含 `/v1` |
| `OPENAI_MODEL` | 无 | 建议设置 | 运行时覆盖 model YAML 中的 `model` 字段 |
| `AIBENCH_API_KEY` | 无 | 可选 | 与 `OPENAI_API_KEY` 二选一 |
| `AIBENCH_BASE_URL` | 无 | 可选 | 与 `OPENAI_BASE_URL` 同义兜底 |
| `AIBENCH_MODEL` | 无 | 可选 | 与 `OPENAI_MODEL` 同义兜底 |
| `AIBENCH_RETRY_MAX` | `3` | 可选 | HTTP/DB 最大尝试次数（含首次） |
| `AIBENCH_RETRY_BACKOFF` | `1.0` | 可选 | 指数退避基数（秒）+ jitter |
| `AIBENCH_RETRY_BACKOFF_MAX` | `20.0` | 可选 | 退避上限（秒） |
| `AIBENCH_CASE_RETRY` | `2` | 可选 | case 因 **infra_error** 整 case 重跑次数 |
| `AIBENCH_USD_PER_MTOK` | 无 | 可选 | 统一 $/百万 tokens 估算成本 |
| `AIBENCH_USD_PER_MTOK_INPUT` | `0.5` | 可选 | 分项时 input 单价 |
| `AIBENCH_USD_PER_MTOK_OUTPUT` | `1.5` | 可选 | 分项时 output 单价；未设 blended 时用 (in+out)/2 |

**禁止**将含真实密钥的 `.env` 提交到 git。

---

## 7. 生产配置体系 `configs/`

详见 [`configs/README.md`](../configs/README.md)。Mock **不**在此目录。

### 7.1 布局

| 路径 | 用途 |
|------|------|
| `agents/openai_compat.yaml` | 单轮 JSON 写文件（默认生产 Agent） |
| `agents/tool_loop.yaml` | 多步 list/read/write/bash/submit |
| `agents/shell.yaml` | 外部 CLI 包装；须配置 `command_template` |
| `models/glm52.yaml` | 默认 `GLM-5.2`；URL/key 来自环境 |
| `models/glm51.yaml` | 可选对照 |
| `models/qwen37.yaml` | 可选对照 |
| `runs/baseline.yaml` | 单次：openai_compat + glm52，`case_workers: 4` |
| `runs/baseline-tool-loop.yaml` | 单次：tool_loop + glm52，`case_workers: 2` |
| `runs/ablation-matrix.yaml` | 消融：openai_compat vs tool_loop |

### 7.2 模型 YAML 字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `name` | string | 人类可读名称 |
| `provider` | string | 如 `openai_compat` |
| `model` | string | 模型 ID；可被 `OPENAI_MODEL` 覆盖 |
| `base_url` | string \| null | `null` 时用 `OPENAI_BASE_URL` / `AIBENCH_BASE_URL` |
| `api_key_env` | string | 读 Key 的环境变量名，默认 `OPENAI_API_KEY` |
| `temperature` | number | 采样温度，生产常用 `0` |
| `max_tokens` | int | 单次生成上限 |
| `extra` | object | 预留扩展 |

**示例（glm52.yaml）**

```yaml
name: glm-5.2
provider: openai_compat
model: GLM-5.2
base_url: null
api_key_env: OPENAI_API_KEY
temperature: 0
max_tokens: 8192
extra: {}
```

### 7.3 Agent YAML 字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `name` | string | 写入结果表的 Agent 名称 |
| `version` | string | Agent 版本字符串 |
| `adapter` | string | 注册表键：`openai_compat` / `tool_loop` / `shell` / `mock` |
| `description` | string | 可选说明 |
| `options` | object | 适配器专有选项（见 §12） |

### 7.4 单次 Run YAML 字段

| 字段 | 生产 baseline 示例 | 说明 |
|------|-------------------|------|
| `experiment_name` | `prod-baseline` | 实验名 |
| `algorithm_name` | `Baseline` | 算法名（结果表） |
| `algorithm_version` | `v1.0` | 算法版本 |
| `budget_axis` | `steps` | 预算轴类型 |
| `budget_value` | `max_steps=40, max_wall_time_s=300` | 预算描述 |
| `branches` | `1` | 分支数 |
| `max_attempts` | `1` | 每 case 尝试次数 |
| `max_steps` | `40` | Agent 最大步数 |
| `max_wall_time_s` | `300` | 每 case 墙钟上限（秒） |
| `selection_strategy` | `first-submit` | 多分支选择策略标签 |
| `case_set` | `auto-v0` | 默认 case 集 |
| `benchmark_name` | `AI-Coding-Assist` | Benchmark 名 |
| `grouping` | `task_type` | 分组口径 |
| `agent_config` | `configs/agents/openai_compat.yaml` | 默认 Agent |
| `model_config` | `configs/models/glm52.yaml` | 默认模型 |
| `case_workers` | `4` | case 级并行度 |

### 7.5 消融矩阵 YAML 字段

```yaml
case_set: auto-v0
baseline_experiment: openai-compat-glm52
parallel: 1
allow_weak_grader: false
runs:
  - experiment_name: openai-compat-glm52
    algorithm_name: Baseline
    agent_config: configs/agents/openai_compat.yaml
    model_config: configs/models/glm52.yaml
    run_id: prod-oc-glm52
    run_config: configs/runs/baseline.yaml
  - experiment_name: tool-loop-glm52
    algorithm_name: Baseline
    agent_config: configs/agents/tool_loop.yaml
    model_config: configs/models/glm52.yaml
    run_id: prod-tl-glm52
    run_config: configs/runs/baseline-tool-loop.yaml
```

| 字段 | 说明 |
|------|------|
| `case_set` | 矩阵默认测评集 |
| `baseline_experiment` | 计算「相对基线收益」的实验名 |
| `parallel` | 实验行并行（可被 CLI `--parallel` 覆盖） |
| `allow_weak_grader` | 是否保留 weak_grader case |
| `runs[]` | 每一项 = 一次独立实验行 |
| `runs[].experiment_name` | 报告中的实验名 |
| `runs[].algorithm_name` | 覆盖写入聚合表 |
| `runs[].agent_config` / `model_config` | 配置路径 |
| `runs[].run_id` | 子 run ID |
| `runs[].case_set` | 可选，覆盖顶层 |
| `runs[].run_config` | 可选，完整 run yaml（预算等） |

---

## 8. CLI 命令完整参考（参数清单 + 作用）

入口：`uv run python -m aibench <cmd>` 或安装后的 `aibench`。  
源码权威：`src/aibench/cli.py`。下表对每个参数给出 **类型 / 默认 / 作用 / 影响**。

### 8.0 公共约定

| 约定 | 说明 |
|------|------|
| Case set 解析 | ① `benchmarks/ai_coding/cases/<name>/` ② 否则 `tests/fixtures/case_sets/<name>/` |
| 路径 | 相对路径相对**仓库根**解析（agent/model/run-config） |
| `.env` | `main()` 启动时 `load_dotenv()` |
| Exit 码 | 一般 0 成功；校验/无产出常为 1；审计 `--fail-on-error` 与 secrets 不 clean 为 2 |

### 8.1 `run` — 单次 Benchmark

**作用**：加载一组 case，按指定 Agent/模型执行，判分并写出 run 目录。

| 参数 | 类型 | 默认 | 作用说明 |
|------|------|------|----------|
| `--run-config` | Path | `configs/runs/baseline.yaml` | 实验元数据 + 预算（max_steps、wall time）+ 默认 case_set/agent/model + `case_workers` |
| `--agent` | Path | run-config 的 `agent_config` | 覆盖 Agent YAML；决定 adapter 与 options（如 system_prompt） |
| `--model` | Path | run-config 的 `model_config` | 覆盖模型 YAML；`model` 可再被 `OPENAI_MODEL` 覆盖 |
| `--case-set` | str | run-config.case_set 或 `auto-v0` | 测评集名称 |
| `--run-id` | str | `{experiment_name}-{uuid8}` | 本次 run 唯一 ID，进入目录名与 summary |
| `--output-root` | Path | `runs/` | 结果根；实际目录为 `{benchmark}__{timestamp}_{run_id}` |
| `--workers` | int | run-config.`case_workers` 或 `1` | **case 级**并行；每 case 独立 workspace 与 Agent 实例 |

**不通过 CLI 传、但来自 run YAML 的执行参数**：`max_steps`、`max_wall_time_s`、`max_attempts`、`branches`、`selection_strategy`、`algorithm_*`、`budget_*` 等（见 §7.4）。

### 8.2 `validate-cases`

**作用**：对照 `case.schema.json` 校验集内每条 case，并检查 `case_id` 唯一。

| 参数 | 类型 | 默认 | 作用说明 |
|------|------|------|----------|
| `--case-set` | str | `auto-v0` | 目标集；失败打印错误列表并 exit 1 |

### 8.3 `check-summary`

**作用**：检查某次 run 的 `summary.json` 是否含结果表约定的核心键（对接总表）。

| 参数 | 类型 | 默认 | 作用说明 |
|------|------|------|----------|
| `run_dir` | Path（位置参数） | 必填 | run 输出目录；读取其中 `summary.json` |

### 8.4 `extract-cases` — JSON 会话 → 草稿

**作用**：从规范化会话导出构建草稿（不连库）。

| 参数 | 类型 | 默认 | 作用说明 |
|------|------|------|----------|
| `--input` | Path | 必填 | 文件内容为 list 或 `{sessions: [...]}` |
| `--output-dir` | Path | 必填 | 每个草稿写 `{case_id}.json` |
| `--max-cases` | int | `50` | 写出草稿上限（过滤后截断） |

### 8.5 `extract-from-db` — MySQL `llm_chat_records` → 草稿

**作用**：扫描会话表，归一化 history，产出草稿。

| 参数 | 类型 | 默认 | 作用说明 |
|------|------|------|----------|
| `--db-url` | str | `AIBENCH_DB_URL` 或 `DATABASE_URL` | SQLAlchemy URL；都未设则 RuntimeError |
| `--output-dir` | Path | 必填 | 草稿输出目录 |
| `--limit` | int | `300` | **SQL 扫描行数上限**（读库预算） |
| `--max-cases` | int | `30` | **写出草稿条数上限**（写出预算） |
| `--min-messages` | int | `3` | `JSON_LENGTH(full_history)` 下限，过滤过短 |
| `--max-messages` | int | `60` | 同上上限，过滤超长轨迹 |
| `--all-agents` | flag | 关 | 默认 `only_opencode=true`（User-Agent 含 opencode）；打开则不限 |
| `--require-gold` | flag | 关 | 只保留能从 assistant 抽出代码块的草稿 |
| `--since` | str | 无 | `start_time >=`，`YYYY-MM-DD` |
| `--until` | str | 无 | `start_time <`，`YYYY-MM-DD` |
| `--export-raw` | Path | 无 | 额外写 meta 清单（id、预览、是否有 gold 等） |

### 8.6 `filter-drafts` — 规则（+ 可选 LLM）筛选

**作用**：降低非编程/噪声会话进入生成阶段的比例。

| 参数 | 类型 | 默认 | 作用说明 |
|------|------|------|----------|
| `--input-dir` | Path | 必填 | 输入草稿目录（`*.json`） |
| `--output-dir` | Path | 必填 | keep 写出目录 |
| `--dropped-dir` | Path | 无 | drop 备份；写入时附 `_filter` 理由 |
| `--report` | Path | 无 | `{kept, dropped, items[]}` 报告 |
| `--llm-soft` | flag | 关 | 规则 keep 后再调用 LLM 软过滤（需 `OPENAI_*`）；最终 keep=软过滤结果 |

**规则逻辑摘要**（`filter_rules.py`）：先匹配 drop 模式（巡检、HEARTBEAT、完成度评测、闲聊、纯解释、日志 dump…）直接丢；否则按编程关键词、代码围栏、产品名、工具名累计 score，过阈值才 keep。

### 8.7 `generate-cases` — 草稿 → Schema case

**作用**：生成可机评 case（默认 LLM 造 stub+pytest+script）。

| 参数 | 类型 | 默认 | 作用说明 |
|------|------|------|----------|
| `--input-dir` | Path | 必填 | 草稿目录 |
| `--output-dir` | Path | 必填 | 输出 case 目录（目录名即后续 case-set 名） |
| `--heuristic-only` | flag | 关 | 不调 LLM，仅 `heuristic_case_from_draft` |
| `--max-cases` | int | `50` | 成功写出的最大条数 |
| `--filter` | flag | 关 | 生成前再跑 `rule_filter_draft`，不 keep 则跳过 |
| `--workers` | int | `1` | 并行生成；内部会对最多约 `max_cases×3` 个草稿尝试 |
| `--secrets-scan` | flag | 关 | 生成后 `scan_case_dir`，写 `_secrets_scan.json` |
| `--audit` | flag | 关 | 生成后对每条 `audit_case` + `annotate` metadata |

**行为细节**：默认 LLM 最多尝试 2 次，失败则 print fallback 并启发式；schema 非法则 skip；最终 `n_ok==0` 时 exit 1。元数据常含 `generation=llm|heuristic`、`review_status=needs_review`。

### 8.8 `ablation` — 矩阵消融

**作用**：同一 case set 上按矩阵跑多组 Agent/模型，汇总对比表。

| 参数 | 类型 | 默认 | 作用说明 |
|------|------|------|----------|
| `--matrix` | Path | 必填 | 矩阵 YAML（`runs[]`） |
| `--output-root` | Path | `runs/` | 生成 `ablation_<timestamp>/` |
| `--case-set` | str | 矩阵 `case_set` | CLI 覆盖矩阵默认集 |
| `--allow-weak-grader` | flag | 关 | 默认 **剥离** `metadata.weak_grader=true` 的 case 再跑 |
| `--parallel` | int | `1` | 矩阵行（实验）并行度 |
| `--baseline-experiment` | str | 矩阵字段或无 | 用于计算「相对基线收益」百分点 |
| `--export-csv` | flag | 关 | 写 `ablation_overview.csv` |
| `--export-xlsx` | flag | 关 | 写 xlsx（依赖 openpyxl） |

### 8.9 `promote` — 人工门控发布

**作用**：把候选集中**通过门控**的 case 复制到正式集（如 `prod-v0`），并改 `review_status=published`。

| 参数 | 类型 | 默认 | 作用说明 |
|------|------|------|----------|
| `--from-set` | str | `auto-v0` | 源集 |
| `--to-set` | str | `prod-v0` | 目标集 |
| `--case-id` | str，可重复 | 无 | 显式指定；省略则对源集全部尝试门控 |
| `--allow-non-script` | flag | 关 | 默认要求 `grader.mode==script` |
| `--allow-secrets` | flag | 关 | 默认 secrets 有 finding 则跳过 |
| `--require-audit` | flag | 关 | 额外要求 `audit_case(...).ok` |
| `--dry-run` | flag | 关 | 不写文件，只返回报告 |
| `--report` | Path | 无 | 把 promote 报告 JSON 落盘 |

**门控顺序（每条 case）**：schema → script 模式（可关）→ 非 weak_grader → secrets（可关）→ 可选 validity audit → 写盘并复制 snapshots。

### 8.10 `audit-cases` — 科学效度审计

**作用**：对 case set 跑 §14 全部门禁，输出汇总；可选回写 metadata。

| 参数 | 类型 | 默认 | 作用说明 |
|------|------|------|----------|
| `--case-set` | str | 必填 | 目标集 |
| `--report` | Path | 无 | 完整审计 JSON（含每 case issues） |
| `--annotate` | flag | 关 | 写入 `difficulty`、`fingerprint`、`validity_ok`、`validity_issues` |
| `--fail-on-error` | flag | 关 | `failed>0` 时 exit **2**（便于 CI） |

### 8.11 `secrets-scan`

**作用**：用正则扫描 prompt / context.files / gold_files 中疑似密钥。

| 参数 | 类型 | 默认 | 作用说明 |
|------|------|------|----------|
| `--case-set` | str | 无 | 与 `--input-dir` 二选一 |
| `--input-dir` | Path | 无 | 直接扫目录下 `*.json` |
| `--report` | Path | 无 | 报告路径；stdout 也打印 |

规则示例：`sk-…`、`AKIA…`、PRIVATE KEY、password/api_key 赋值、Bearer token。  
`clean=false` → exit 2。

### 8.12 `snapshot-skeleton`

| 参数 | 类型 | 默认 | 作用说明 |
|------|------|------|----------|
| `--case-set` | str | 必填 | 将 `context.files` 落到 `snapshots/<case_id>/`，并设 `workspace.mode=mixed` |

### 8.13 `export-ablation`

| 参数 | 类型 | 默认 | 作用说明 |
|------|------|------|----------|
| `--ablation-dir` | Path | 必填 | 已有消融目录 |
| `--csv` | flag | **True** | 导出 CSV |
| `--xlsx` | flag | 关 | 导出 XLSX |

---

## 9. 一键脚本参考

### 9.1 `scripts/run_benchmark.sh`

| 参数 | 默认 | 说明 |
|------|------|------|
| `--agent` | `configs/agents/openai_compat.yaml` | Agent |
| `--model` | `configs/models/glm52.yaml` | 模型 |
| `--case-set` | `auto-v0` | Case 集 |
| `--run-config` | `configs/runs/baseline.yaml` | Run 配置 |
| `--run-id` | 空（自动） | 运行 ID |
| `--workers` | 空（用 run-config） | case 并行 |

行为：先 `validate-cases`，再 `aibench run`。自动 source 项目根 `.env`。

### 9.2 `scripts/e2e_pipeline.sh`

| 参数 | 默认 | 说明 |
|------|------|------|
| `--dry-run` | 关 | 仅 fixture；不访问 DB/LLM 生成；mock 消融 |
| `--skip-extract` | 关 | 跳过抽库，复用 `drafts-from-db` |
| `--heuristic-only` | 关 | 生成阶段不调 LLM |
| `--limit` | `100` | 抽库扫描行数上限 |
| `--max-cases` | `8` | 最多生成 case 数；抽库 max-cases=`N×3` |
| `--matrix` | `configs/runs/ablation-matrix.yaml` | 消融矩阵 |
| `--output-root` | `runs/` | 输出根 |
| `--workers` | 空 | 传给 `generate-cases --workers` |

真链路目录约定：

| 路径 | 用途 |
|------|------|
| `benchmarks/ai_coding/cases/drafts-from-db/` | 原始草稿 |
| `.../drafts-kept/` | 筛选后 |
| `.../drafts-dropped/` | 丢弃备份 |
| `.../auto-v0/` | 候选 case 集 |
| `runs/ablation_*/` | 消融汇总与子 run |

### 9.3 质量脚本

| 脚本 | 作用 |
|------|------|
| `scripts/lint.sh` | ruff format + check --fix |
| `scripts/install-hooks.sh` | 安装 pre-commit |

---

## 10. Case 协议与 Schema

权威 Schema：`benchmarks/ai_coding/schemas/case.schema.json`（JSON Schema draft 2020-12）。

### 10.1 必填字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `case_id` | string | 唯一 ID |
| `schema_version` | string | Schema 版本 |
| `task_type` | enum | `bugfix` \| `feature` \| `refactor` \| `explain_to_edit` \| `test_gen` \| `pairwise` |
| `language` | string | 主语言 |
| `prompt` | string | 任务描述（给 Agent） |
| `context` | object | 至少含 `files` |
| `grader` | object | 至少含 `mode` |

### 10.2 `context`

| 字段 | 说明 |
|------|------|
| `files[]` | `{path, content}` 列表；inline 工作区文件 |
| `notes` | 可选备注 |
| `workspace` | 见 §11 |

### 10.3 `grader`

| 字段 | 说明 |
|------|------|
| `mode` | `script` \| `gold` \| `llm_judge` \| `composite` |
| `command` | script 模式命令（白名单：`pytest` / `python`） |
| `gold_files` | gold 模式期望文件 |
| `match` | `exact` \| `normalized` \| `contains_key_lines` |
| `key_lines` | 关键行列表 |
| `judge_rubric` / `judge_threshold` | llm_judge 用 |

### 10.4 `metadata`（常用扩展）

| 键 | 含义 |
|----|------|
| `source` | 来源标签 |
| `source_session_id` | 原始会话 ID |
| `generation` | `llm` / `heuristic` |
| `review_status` | 如 `needs_review` |
| `weak_grader` | bool |
| `difficulty` | `easy` / `medium` / `hard`（审计注解） |
| `fingerprint` | case 指纹 |
| `validity_ok` | 审计是否通过 |
| `tags` / `split` | 标签与划分 |

`metadata` 允许 `additionalProperties`，便于流水线注解。

---

## 11. Workspace 物化规则

`context.workspace`：

| 字段 | 默认 | 说明 |
|------|------|------|
| `mode` | `inline` | `inline` \| `snapshot` \| `git` \| `mixed` |
| `snapshot.path` | 无 | 相对 case-set 的目录或 tar/zip |
| `git.url` / `ref` / `subdir` / `sparse_paths` | 无 | 固定仓库版本 |
| `setup_commands` | `[]` | 落盘后 shell（cwd=workspace） |
| `env` | `{}` | 注入 setup 的环境变量 |
| `strict` | `true` | snapshot/git 失败则 case `infra_error` |

**应用顺序**：清空 workspace → snapshot/git 基线 → **inline `files` 覆盖** → `setup_commands`。

---

## 12. Agent 适配器

| adapter | 配置文件 | 行为摘要 |
|---------|----------|----------|
| `openai_compat` | `configs/agents/openai_compat.yaml` | 单轮 Chat；要求返回 `{"files":[...],"message":"..."}` 并写入 workspace |
| `tool_loop` | `configs/agents/tool_loop.yaml` | 多步工具：`list` / `read` / `write` / `bash` / `submit` |
| `shell` | `configs/agents/shell.yaml` | 外部 CLI；占位符 `{workspace}` `{prompt_file}` `{case_id}` `{max_steps}` |
| `mock` | **仅** `tests/fixtures/configs/agents/mock.yaml` | 单测 / dry-run |

### 12.1 `openai_compat` options

| 选项 | 说明 |
|------|------|
| `system_prompt` | 系统提示（要求 JSON 文件重写协议） |
| `max_tokens` | 请求上限（可被模型配置影响） |

### 12.2 `tool_loop` options

| 选项 | 说明 |
|------|------|
| `allow_bash` | 是否允许 bash 工具 |
| `max_tokens` | 每步请求上限 |
| `system_prompt` | 工具调用 JSON 协议说明 |

### 12.3 `shell` options

| 选项 | 说明 |
|------|------|
| `command_template` | 外部命令模板（**生产须自行配置**，默认为空） |
| `env` | 额外环境变量 |

退出码 0 视为 agent 完成；修改须落在 `{workspace}` 下。

---

## 13. 判分（Grading）

| mode | 成功条件 |
|------|----------|
| `script` | 执行 `command` 且 exit code = 0 |
| `gold` | 文件内容按 `match` 与 gold / `key_lines` 匹配 |
| `llm_judge` | 模型评分 ≥ `judge_threshold` |
| `composite` | 优先 script，再回退 gold 等组合逻辑 |

Script 命令白名单限制（`pytest` / `python`），防止任意 shell 注入。  
主指标仅统计 **非 infra_error** 的 case。

---

## 14. 科学效度（Scientific Validity）：定义、门禁与逻辑

实现：`src/aibench/validity.py`。命令：`aibench audit-cases`。

### 14.1 「科学效度」在本项目中的含义

**不是**心理测量学里完整的构念效度/外部效度论证，而是评测工程上的一组 **可自动执行的 case 质量门禁**，用来保证：

| 目标 | 若不做门禁会怎样 |
|------|------------------|
| **可区分（discriminating）** | 初始 stub 已能过测 → 任何 Agent 都「成功」，指标虚高 |
| **无答案泄漏（uncontaminated）** | gold/关键行已在 context → 复制即可满分 |
| **可理解的任务** | prompt 过短/噪声 → 失败不反映模型能力 |
| **可复现 / 可去重** | 无指纹 → 无法核对 case 集是否漂移、是否重复 |
| **可分层解读** | 无难度标签 → 无法看 easy/hard 分层成功率 |

一句话：尽量让 `task_success_rate` 的差异来自 **Agent/模型能力**，而非坏题或泄漏。

`CaseValidityReport.ok == True` **当且仅当** 不存在 `severity=error` 的 issue（warn 不影响 ok）。

### 14.2 门禁清单

| 门禁 | issue code | 级别 | 阻断 ok | 说明 |
|------|------------|------|---------|------|
| Stub 必须失败 | `stub_fail_gate` | error | 是 | 见 §14.3.1 |
| Gold 污染 | `contamination_gold_in_context` | error | 是 | gold 全文已在 context |
| Key line 污染 | `contamination_keyline_in_context` | error | 是 | gold 模式下关键行已在 context |
| Prompt 过短 | `prompt_too_short` | error | 是 | `len(strip)<20` |
| Prompt 大代码块 | `prompt_contains_large_code_fence` | warn | 否 | 疑似泄漏，人工看 |
| 弱 grader 标记 | `weak_grader_flag` | warn | 否 | script 却标 weak_grader |
| 重复指纹 | `duplicate_fingerprint` | warn | 否 | 集内 fingerprint 冲突 |
| 难度 | （写入 checks/metadata） | 注解 | — | easy/medium/hard |
| 指纹 | fingerprint / content_fingerprint | 注解 | — | 去重与复现 |

### 14.3 门禁逻辑详解

#### 14.3.1 Stub-fail（script 题核心）

```text
若 grader.mode != "script" 或 command 为空:
    → 跳过，detail=skipped_non_script，视为通过

否则:
    tmp = 临时目录
    materialize_workspace(case)     # 仅初始现场，未经 Agent
    grade = grade_case(case, ws)    # 跑白名单命令（pytest/python）
    若 grade.infra_error:  失败（环境/命令问题）
    若 grade.passed:       失败 stub_passed_grader  # 初始已过测
    否则:                  通过 stub_failed_as_expected
```

**意图**：Agent 必须做出有效修改才能得分。

#### 14.3.2 Contamination（答案污染）

```text
blob = 拼接 context.files 的 path + content

对每个 gold_file:
  body = gold.content.strip()
  若 len(body) >= 40 且 body 是 blob 的子串:
    → error contamination_gold_in_context

对每个 key_line（仅 grader.mode == "gold"）:
  s = line.strip()
  若 len(s) >= 8 且 s 是 blob 的子串:
    → error contamination_keyline_in_context

若 prompt 匹配「较大 ``` 代码围栏」且含 "implement"（忽略大小写）:
    → warn prompt_contains_large_code_fence
```

#### 14.3.3 Prompt 过短

`len((prompt or "").strip()) < 20` → `error prompt_too_short`。

#### 14.3.4 难度启发式（不 fail）

```text
score = 文件数 + (路径含 test 的文件中 def test_ 个数) + (总行数 // 40)
score ≤ 4  → easy
score ≤ 12 → medium
else       → hard
```

用于报告分层，不是严格能力标定。

#### 14.3.5 指纹与集级去重

```text
case_fingerprint = sha256(f"{task_type}|{prompt.strip()}|{'|'.join(sorted paths)}")[:16]
content_fingerprint(set) = sha256(sorted "case_id:fp" 行)[:16]

同集内相同 fingerprint 的多条:
  → 各自 warn duplicate_fingerprint（列出重复 id 列表）
```

`content_fingerprint` 会写入 run 的 `run_manifest.json` / summary，便于复现核对「是否同一 case 集」。

#### 14.3.6 `annotate` 写回字段

| metadata 键 | 值 |
|-------------|-----|
| `difficulty` | easy/medium/hard |
| `fingerprint` | 16 位 hex |
| `validity_ok` | bool |
| `validity_issues` | issue 对象列表 |

### 14.4 与 `promote` 发布门控的关系

| 能力 | audit-cases | promote |
|------|-------------|---------|
| stub/污染/短 prompt | ✅ | 仅当 `--require-audit` |
| JSON Schema | 加载时 | ✅ 始终 |
| 要求 script grader | — | 默认是（`--allow-non-script` 关） |
| 拒绝 weak_grader | warn | ✅ 跳过 |
| secrets 扫描 | 可另跑 secrets-scan | 默认拦截（`--allow-secrets` 关） |
| 改 review_status | 否 | 写 `published` |

推荐正式发布：

```bash
uv run python -m aibench audit-cases --case-set auto-v0 \
  --annotate --report /tmp/audit.json --fail-on-error

uv run python -m aibench promote --from-set auto-v0 --to-set prod-v0 \
  --require-audit --case-id my_case
```

### 14.5 报告侧「可科学解读」增强（非 case 门禁）

| 项 | 位置 | 作用 |
|----|------|------|
| Wilson 95% CI | summary / 分层 | 小样本成功率区间 |
| 有效 case | success_rate 分母 | 排除 `infra_error` |
| 分层成功率 | task_type / difficulty | 结构解读 |
| failure_diagnostics | summary | 失败类型聚合 |

这些不改变单 case `ok`，但提高对汇总指标的解读可信度。

---

## 15. 重试与并行

### 15.1 重试

| 机制 | 配置 | 作用 |
|------|------|------|
| HTTP / DB | `AIBENCH_RETRY_MAX` 等 | 超时、连接错误、空 content |
| Case infra | `AIBENCH_CASE_RETRY` | materialize / agent 基础设施失败整 case 重跑 |

### 15.2 并行层级

| 层级 | 参数 | 默认 | 说明 |
|------|------|------|------|
| Case | `run --workers` / run yaml `case_workers` | 1 或配置值 | 每 case 独立 Agent 与 workspace |
| 生成 | `generate-cases --workers` | 1 | LLM/启发式并行 |
| 消融 | `ablation --parallel` / 矩阵 `parallel` | 1 | 多实验行并行 |

正确性约定：并行不得改变单 case 语义；mock 上 workers=1 与 workers>1 成功率应一致。

---

## 16. 运行产物与结果表映射

### 16.1 单次 Run 目录

`runs/<Benchmark>__<timestamp>_<run_id>/`

| 文件 | 用途 |
|------|------|
| `run_manifest.json` | 配置快照、`case_set_fingerprint` |
| `summary.json` | 聚合指标、CI、分层、Token、成本、失败诊断 |
| `tables.json` | `overview_row` + `general_row`（对齐结果表） |
| `results.jsonl` | 每 case 一行 |
| `report.md` | 人类可读报告（含单行综述表 + 关键字段 + 分层） |
| `cases/<id>/` | workspace 快照与 `result.json` 明细 |

### 16.2 消融目录

`runs/ablation_<timestamp>/`

| 文件 | 用途 |
|------|------|
| `ablation_report.md` | **多行综述对比表**（汇报首选） |
| `ablation_summary.json` | 机器汇总（含各 run 的 overview/general） |
| `ablation_overview.csv` / `.xlsx` | 可选导出 |
| 子 run 目录 | 各组完整结果 |

### 16.3 `overview_row`（项目效果综述表）

| 列 | 来源 |
|----|------|
| 算法名称 | `algorithm_name` |
| Agent与模型 | `{agent}｜main={model}` |
| 基础/主模型 | `main_model` |
| Benchmark | `benchmark_name` |
| Case数 | `case_count` |
| 主指标名称 | 固定 `task_success_rate` |
| 主指标值 | 成功率百分比字符串 |
| 总体耗时(h) | 总墙钟小时 |
| 总体Token消耗 | `total_tokens` |
| 相对基线收益 | 消融相对 baseline 的百分点（如 `+3.2pp`）；单次 run 常为 null |

### 16.4 `summary.json` / `general_row` 核心字段

覆盖设计「通用结果总表」中本 harness 可采集的字段，包括：

- 实验标识：`run_id`、`experiment_name`、`experiment_time`、`code_version`
- Benchmark 口径：`benchmark_name`、`case_set`、`case_count`、`effective_case_count`、`grouping`
- 算法配置：预算轴/值、分支、attempts、steps、选择策略
- Agent 与模型：名称版本、主模型、组合摘要、采样参数
- 主质量：成功数/率、完成数/率、空 patch、infra 错误、Wilson CI
- 成本效率：总 Token、均值、估算 USD
- 时间效率：总墙钟、吞吐、平均耗时
- Agent 行为：总 steps、模型调用次数

**无数据源的列**（Fusion 时间拆解、投机 Oracle 等）保持 `null`，不编造。

### 16.5 如何使用输出

| 目的 | 看什么 |
|------|--------|
| 汇报 / 选型 | `ablation_report.md` 或 `tables.json.overview_row` |
| 归因 | `report.md` 失败诊断、`results.jsonl`、`cases/<id>/result.json` |
| 复现 | `run_manifest.json` + case set fingerprint + 配置路径 |
| 入库 / 看板 | 读 JSON/CSV，按列名映射到总表 |

---

## 17. 消融（Ablation）

### 17.1 定义

**消融** = 固定测评集，只改变 Agent/模型（及算法标签），跑多组对照并汇总。  
**不是** 删除模型组件的狭义学术消融，而是 **对照实验矩阵**。

### 17.2 当前生产矩阵

`configs/runs/ablation-matrix.yaml`：

1. `openai_compat` + `GLM-5.2`（基线实验名 `openai-compat-glm52`）
2. `tool_loop` + `GLM-5.2`

可选第二模型行已在 YAML 中注释，可按网关能力解开。

### 17.3 默认行为

- 剔除 `weak_grader=true` 的 case（除非 `--allow-weak-grader`）
- 可计算相对基线成功率收益（百分点）
- 可并行实验行、导出 CSV/XLSX

---

## 18. 与初始设计文件的关系

| 设计文件 | 角色 |
|----------|------|
| `docs/html/agentic-scaling-benchmark.html` | 结果表与评测协议的 **设计报告**（统一粒度、综述表、通用表、落盘建议、落地阶段） |
| `docs/html/tables.html`（源 `_src/tables.md`） | **字段字典**（列名、含义、类型；HTML 优化表名/说明与多级表头） |

本仓库是 **执行与填表实现**：

```text
设计：一行 = 同一 Benchmark + case set + 一个算法配置 + 预算 + 一次 run
实现：aibench run / ablation 产出对应行
主指标：task_success_rate（AI 编程半确定性）
空字段：无数据源时填 null，不编造 Fusion 专用列
```

| 设计要求 | 实现映射 |
|----------|----------|
| 一行 = Benchmark + case set + 算法配置 + 预算 + run | 一次 `aibench run` 或消融矩阵中的一行 |
| 项目效果综述表 | `overview_row` / `ablation_report.md` 多行表 |
| 通用结果总表 | `summary.json` 核心字段 + `general_row` |
| 落盘 `runs/...` | runner 标准目录结构 |
| 置信区间 | Wilson 95% CI |
| 有效 case | 排除 `infra_error` |

设计页回答「表应该长什么样」；本仓库回答「如何从会话生成 case 并填出这些行」。

---

## 19. 仓库目录结构

```text
agent-scaling-benchmark/
├── README.md
├── pyproject.toml
├── .env.example
├── configs/                           # 生产配置（无 mock）
│   ├── agents/
│   ├── models/
│   └── runs/
├── docs/
│   ├── html/                          # **统一 HTML 文档站**
│   │   ├── index.html
│   │   ├── project-overview.html
│   │   ├── reference.html / tables.html
│   │   ├── _src/tables.md             # 字段字典 Markdown 源
│   │   └── …
│   ├── REFERENCE.md                   # 本文（Markdown 源）
│   ├── USER_GUIDE.md
│   ├── REMAINING_WORK.md
│   ├── design/
│   └── implementation/
├── benchmarks/ai_coding/
│   ├── schemas/case.schema.json
│   └── cases/                         # auto-v0、drafts-* 等
├── scripts/
│   ├── e2e_pipeline.sh
│   ├── run_benchmark.sh
│   ├── lint.sh
│   └── install-hooks.sh
├── src/aibench/                       # 可执行 harness
├── tests/                             # 单测 + fixtures（含 mock）
└── runs/                              # 实验结果（本地生成，gitignore）
```

---

## 20. 代码质量与 CI

```bash
./scripts/install-hooks.sh
./scripts/lint.sh
uv run ruff check --fix src tests
uv run ruff format src tests
uv run pytest tests/ -q
```

| 项 | 位置 |
|----|------|
| Ruff 配置 | `pyproject.toml` → `[tool.ruff]` |
| pre-commit | `.pre-commit-config.yaml` |
| CI | `.github/workflows/ci.yml`（ruff + pytest + dry-run） |

约定：中文用户可见字符串允许全宽字符（RUF001/002/003 已 ignore）。

---

## 21. 扩展点

| 扩展 | 做法 |
|------|------|
| 新 Agent | 实现 `AgentAdapter`，在 `aibench.agents.registry` 注册，新增 agent YAML |
| 新模型 | 新增 `configs/models/*.yaml`，填 `model` 与环境 |
| 新对照实验 | 编辑 `ablation-matrix.yaml` 的 `runs[]` |
| 外部 CLI Agent | 配置 `shell.yaml` 的 `command_template` |
| 正式集 | `promote --require-audit` 到 `prod-v0` 或自定义名称 |
| 成本口径 | 设置 `AIBENCH_USD_PER_MTOK*` |

---

## 22. 常见问题

**Q: 只生成 case，不要消融？**  
只跑 extract → filter → generate → validate/audit。不要跑完整 `e2e_pipeline.sh`。

**Q: auto-v0 能直接当正式集？**  
否。须人工审 + `promote --require-audit` 到 `prod-v0`。

**Q: 不设 limit / max-cases？**  
用默认值，不会全库无限跑（§4.3）。

**Q: 结果表很多空列？**  
正常。仅填充有数据源的质量/成本/时间/Agent 字段；Fusion 专用拆解等为 `null`。

**Q: LLM 生成全失败？**  
检查 `OPENAI_BASE_URL` / `KEY` / `MODEL`；推理模型可能占满 token。生成器失败后会 fallback 启发式（多为 gold，区分度弱）。

**Q: mock 成功率异常高？**  
可能在用弱 gold 集；LLM 生成的 script 集上 mock 应接近 0%。生产路径默认不使用 mock。

**Q: 抽库为空？**  
检查 `AIBENCH_DB_URL`、`--limit`、`--min-messages`、是否需 `--all-agents` / `--require-gold` 与时间窗。

**Q: shell agent 不工作？**  
`command_template` 必须非空且 CLI 可执行；修改须写在 `{workspace}` 下。

---

## 23. 命令速查卡

```bash
# 环境
uv sync --extra dev
cp .env.example .env && set -a && source .env && set +a
./scripts/install-hooks.sh

# 仅生成用例
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

# 单次跑测
./scripts/run_benchmark.sh
# 或
uv run python -m aibench run --run-config configs/runs/baseline.yaml --workers 4

# 消融
uv run python -m aibench ablation \
  --matrix configs/runs/ablation-matrix.yaml \
  --baseline-experiment openai-compat-glm52 \
  --export-csv

# 全流程（含消融）
./scripts/e2e_pipeline.sh --limit 100 --max-cases 8

# 审计与发布
uv run python -m aibench audit-cases --case-set auto-v0 --annotate --fail-on-error
uv run python -m aibench promote --from-set auto-v0 --to-set prod-v0 \
  --require-audit --case-id <id>

# 质量
./scripts/lint.sh
uv run pytest tests/ -q

# 离线冒烟
./scripts/e2e_pipeline.sh --dry-run
```

---

*文档随代码演进；冲突时以仓库 CLI `--help`、`configs/` 现行内容与 `case.schema.json` 为准。*
