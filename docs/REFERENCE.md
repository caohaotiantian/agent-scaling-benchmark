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
13.5 [区分度分层（Tier）与校准](#135-区分度分层tier与校准)
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
| `OPENAI_MODEL` | 无 | 可选 | **兜底**：仅当 model YAML 的 `model` 为空时生效（配置优先） |
| `AIBENCH_API_KEY` | 无 | 可选 | 与 `OPENAI_API_KEY` 二选一 |
| `AIBENCH_BASE_URL` | 无 | 可选 | 与 `OPENAI_BASE_URL` 同义兜底 |
| `AIBENCH_MODEL` | 无 | 可选 | 与 `OPENAI_MODEL` 同义兜底 |
| `AIBENCH_RETRY_MAX` | `3` | 可选 | HTTP/DB 最大尝试次数（含首次） |
| `AIBENCH_RETRY_BACKOFF` | `1.0` | 可选 | 指数退避基数（秒）+ jitter |
| `AIBENCH_RETRY_BACKOFF_MAX` | `20.0` | 可选 | 退避上限（秒） |
| `AIBENCH_REQUEST_TIMEOUT` | `240` | 可选 | Agent 单次 LLM 请求超时（秒），上限为该 case 的 `max_wall_time_s`。默认 120 时慢网关下实测 5–9% infra 错误 |
| `AIBENCH_GENERATE_TIMEOUT` | `300` | 可选 | `generate-cases` 单次 LLM 请求超时（秒）；推理模型出整份 case JSON 常需 2 分钟以上 |
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
| `model` | string | 模型 ID；**优先于** `OPENAI_MODEL`（后者仅在此处为空时兜底） |
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
| `--model` | Path | run-config 的 `model_config` | 覆盖模型 YAML；其 `model` 字段优先于 `OPENAI_MODEL` |
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
| `--tier` | `T1..T5` | 草稿 `metadata.tier` | 强制目标层；不传则用 trace 推导的层（§13.5.3） |
| `--min-tier` | `T1..T5` | 无 | 定级低于此层的 case 直接丢弃 |

**行为细节**：默认 LLM 最多尝试 2 次，失败则 print fallback 并启发式；schema 非法则 skip；最终 `n_ok==0` 时 exit 1。元数据常含 `generation=llm|heuristic`、`review_status=needs_review`。

按目标层选用不同的生成 brief（`_TIER_BRIEFS`），产物再经 `settle_tier` 消毒定级，
`metadata` 记录 `tier` / `tier_requested` / `tier_notes` / `capability_axes` / `tier_facts`。
命令结束会打印实际定级分布，例如 `tier distribution: T2=31, T3=12`。

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
| `--annotate` | flag | 关 | 写入 `difficulty`、`tier`、`fingerprint`、`validity_ok`、`validity_issues` |
| `--llm-disclosure-check` | flag | 关 | 对非 T1 用例补一次 LLM 泄露二审（每条一次调用）。正则仍是唯一阻断判据，LLM 只能加 warn |
| `--fail-on-error` | flag | 关 | `failed>0` 时 exit **2**（便于 CI） |

报告含 `tier_distribution`；每条 case 的 `checks` 含 `stub_fail`、`reference_solution`、`tier`。

### 8.9.1 `compose-cases` — 合成 T4 检索用例

**作用**：把已验证用例的实现文件植入另一条已验证用例作为干扰文件，凑齐 T4 的广度要求。

| 参数 | 类型 | 默认 | 作用说明 |
|------|------|------|----------|
| `--from-set` | str | 必填 | 已通过审计的源集合 |
| `--to-set` | str | 必填 | 输出集合 |
| `--target-files` | int | `6` | 每条合成用例的文件总数 |
| `--donors-per-case` | int | `3` | 每条用例取几条其他用例作为干扰来源 |
| `--max-cases` | int | 无 | 上限 |

**两道门禁由构造保持**：宿主的 stub、测试与参考解一字未改，只新增与解无关的文件；干扰文件放在 `vendor/<donor_id>/` 子目录，不会 shadow 宿主的导入，且都不是测试文件，pytest / node 都不会收集。捐赠方的**参考解文件与测试文件被排除**——前者会让干扰文件参与解，后者会被运行器收集并跑起来。

捐赠者按固定顺序轮转而非随机：集合内容每次都变的话，就无法和之前的校准结果对比。

### 8.10.1 `calibrate-cases` — 经验校准（区分度实测）

**作用**：用锚点面板跑 `anchors × repeats` 次，按 case 统计 `p_hat` / `spread` /
`point_biserial` / `flaky`，给出 keep/drop 判定。见 §13.5.5。

| 参数 | 类型 | 默认 | 作用说明 |
|------|------|------|----------|
| `--case-set` | str | 必填 | 待校准集合 |
| `--anchors` | Path | `configs/runs/anchor-panel.yaml` | `anchors:` 列表（name/agent_config/model_config/run_config） |
| `--repeats` | int | `3` | 每个锚点独立重复次数（用于识别 flaky） |
| `--output-root` | Path | `runs/` | 生成 `calibration_<timestamp>/` |
| `--workers` | int | run 配置 | case 并行度 |
| `--p-max` | float | `0.9` | 高于此通过率判送分题 |
| `--p-min` | float | `0.05` | 低于此通过率判无人能过 |
| `--min-rpb` | float | `0.15` | 点二列相关低于此判噪声题 |
| `--reuse-from` | Path | 无 | 上一次的 `calibration.json`；case 内容与锚点面板**都**未变的沿用旧结果，只跑变更/新增的 |

产物：`calibration.json` + `calibration_report.md`。**成本 = 锚点数 × repeats 次全量跑测**，
按需预算。`kept_count==0` 时 exit 1。

### 8.10.2 `select-cases` — 按区分度选题

**作用**：把校准保留的 case 复制成新集合，按 `spread`、`point_biserial` 降序。

| 参数 | 类型 | 默认 | 作用说明 |
|------|------|------|----------|
| `--calibration` | Path | 必填 | `calibration.json` |
| `--from-set` | str | 必填 | 源集合 |
| `--to-set` | str | 必填 | 目标集合 |
| `--max-cases` | int | 无 | 只取区分度最高的前 N 条 |
| `--tier-quota` | str | 无 | 各层占比，如 `T2=0.3,T3=0.4,T4=0.3`。不传则纯按区分度排序，可能整批落在同一层，把单一能力带当成全貌 |
| `--dry-run` | flag | 关 | 不写盘，只回报选择结果 |

写入时把 `metadata.calibration`（`p_hat` / `spread` / `point_biserial` / `attempts`）落到 case 上。

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
| `files[]` | `{path, content, role?}` 列表；inline 工作区文件 |
| `files[].role` | `impl`（默认）\| `test` 可见测试 \| `distractor` 干扰文件 \| `spec` 规格说明 |
| `notes` | 可选备注 |
| `workspace` | 见 §11 |

### 10.3 `grader`

| 字段 | 说明 |
|------|------|
| `mode` | `script` \| `gold` \| `llm_judge` \| `composite` |
| `command` | script 模式命令（白名单：`pytest` / `python`） |
| `gold_files` | gold 模式期望文件；对 T3+ 同时充当**参考解**（§14.3.3） |
| `match` | `exact` \| `normalized` \| `contains_key_lines` |
| `key_lines` | 关键行列表 |
| `judge_rubric` / `judge_threshold` | llm_judge 用 |
| `hidden_tests` | `{path, content}` 列表；**判分时才写入工作区**，Agent 看不到（§13.1） |
| `protected_paths` | 判分前字节必须与 `context.files` 一致的路径；被改判 `reward_hack` |

### 10.4 `metadata`（常用扩展）

| 键 | 含义 |
|----|------|
| `source` | 来源标签 |
| `source_session_id` | 原始会话 ID |
| `generation` | `llm` / `heuristic` |
| `review_status` | 如 `needs_review` |
| `weak_grader` | bool |
| `difficulty` | `easy` / `medium` / `hard`（**已废弃**：体积启发式，实测 93.8% 落在 medium。保留仅为兼容既有 `summary.json` 消费者，分层一律用 `tier`） |
| `tier` | `T1`..`T5` 区分度层级（§13.5.2） |
| `capability_axes` | 该层分离的能力轴，如 `["A1","A5","A6"]` |
| `tier_requested` / `tier_notes` | 请求的目标层，以及每层被拒的原因 |
| `trace_signals` | 源 trace 的过程信号（§13.5.3） |
| `calibration` | `select-cases` 写回的 `p_hat` / `spread` / `point_biserial` |
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
| `allowed_commands` | list | 见 `DEFAULT_ALLOWED_COMMANDS` | bash 允许的程序名白名单。命令在宿主机以 harness 权限执行，这是容器化之前唯一的防线 |
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

### 13.1 隐藏测试与保护路径

实现：`src/aibench/grading.py`。判分前依次执行两步：

1. **保护路径校验** —— `grader.protected_paths` 里每个路径在工作区的字节必须与 `context.files`
   一致。被改判 `reward_hack=true`、成绩 0；路径在 `context.files` 里找不到则记 `infra_error`
   （用例配置错误，不算 agent 失败）。
2. **评分干扰检测**（仅对声明了 `protected_paths` 的用例）—— 工作区里出现用例未附带的
   `conftest.py` / `pytest.ini` / `setup.cfg` / `tox.ini` / `pyproject.toml` / `sitecustomize.py`，
   或新文件里出现 `@pytest.mark.skip` / `pytest.skip(` 等跳过标记，一律判 `reward_hack`。
   `protected_paths` 挡住「改可见测试」，这一条挡住绕过它的路子：注入 conftest 猴补丁被测模块、
   或用 addopts 把失败用例 deselect 掉。
3. **隐藏测试注入** —— `grader.hidden_tests` 的文件此时才写入工作区（仅 `script` / `composite`）。
   Agent 全程看不到它们。

因此 `grader.command` 对 T3+ 用例应为 `python -m pytest -q`（收集整个目录），而不是指定单个测试文件。
`GradeResult.test_pass_ratio` 记录测试函数级通过比例，用于并列打散，不改变二值主指标。

---

## 13.5 区分度分层（Tier）与校准

实现：`src/aibench/tiers.py`、`src/aibench/extract/trace_signals.py`、
`src/aibench/extract/tier_shaping.py`、`src/aibench/calibrate.py`。

### 13.5.1 为什么不用体积启发式

`estimate_difficulty` 按文件数/LOC 打分，在首个自动集上把 **93.8% 判为 medium**，而那批用例
平均 2 步即被解出 —— 它度量的是文件体积，不是所需能力。Tier 改为声明「用例结构上强迫求解者
做什么」，每层配机器可检不变量。

### 13.5.2 层级契约

| 层 | 名称 | 结构不变量（`check_tier_invariants` 逐条校验） | 分离的能力轴 |
|----|------|-----------------------------------------------|--------------|
| T1 | 直接修复 | ≤3 文件；允许题面/注释给出缺陷位置 | 地板锚（几乎全过） |
| T2 | 定位修复 | 2–4 文件；题面**无**机制泄露；stub **无** BUG/FIXME 标记 | A1 定位诊断 |
| T3 | 隐藏规格 | T2 + ≥2 个隐藏测试函数 + 参考解 + 保护可见测试 | A5 规格遵从、A6 抗过拟合 |
| T4 | 跨文件检索 | T3 + ≥5 文件 + ≥1 干扰文件 + 参考解触及 ≥2 文件 + ≥3 隐藏测试 | A2 检索、A4 跨文件一致性 |
| T5 | 迭代自修复 | T4 + ≥4 个隐藏测试函数 | A3 迭代自修复 |

能力轴：`A1` 定位诊断、`A2` 上下文检索、`A3` 迭代自修复、`A4` 跨文件一致性、
`A5` 规格遵从、`A6` 抗过拟合。

### 13.5.3 层级从 trace 推导

`trace_signals.signals_from_messages` 从 `full_history` 解析过程信号：
`read_ops` / `search_ops` / `edit_ops` / `exec_ops` / `test_runs` / `files_touched` /
`repair_rounds`（test→edit→test 循环次数）/ `error_signals`。
`suggest_tier` 自上而下取第一条命中的规则：

| 条件 | 建议层 |
|------|--------|
| `repair_rounds ≥ 2` 且 `files_touched ≥ 3` | T5 |
| `files_touched ≥ 3`，或 `search_ops ≥ 3` 且 `files_read ≥ 5` | T4 |
| `test_runs ≥ 1` 且 `error_signals ≥ 1`，或 `repair_rounds ≥ 1` | T3 |
| 有报错 / `files_read ≥ 2` / 有检索 | T2 |
| 其余 | T1 |

结果写入草稿 `metadata.tier` / `tier_reasons` / `trace_signals`，层级分布因此继承真实生产任务
的难度分布，而非人工配额。

### 13.5.35 多语言

`src/aibench/languages.py` 的 `LanguageSpec` 收拢了所有 per-language 知识：

| 语言 | 测试文件 | 运行命令 | 通过率解析 |
|------|----------|----------|------------|
| python | `test_*.py` / `*_test.py` | `python -m pytest -q` | `N passed/failed/error` |
| javascript / typescript | `*.test.mjs` / `*.spec.ts` 等 | `node --test`（内置，零依赖） | `ℹ pass N` / `# pass N` |

隐藏测试文件名由 `hidden_test_name` 生成，**必须仍被运行器识别为测试**：`clamp.test.mjs` 拆出的隐藏半边是 `clamp_spec.test.mjs` 而不是 `clamp.test_spec.mjs` ——后者不匹配 node 的发现规则，会被静默跳过，用例就只靠冒烟测试通过了，正是隐藏测试要防的那件事。

### 13.5.4 消毒与定级

`tier_shaping.settle_tier` 从 T5 逐层下探，每层用该层所需的变换塑形一份副本，取**第一层不变量
成立**的作为标签：

- `strip_defect_markers` —— 去掉实现文件里的 `# BUG` / `# FIXME`（bugfix 任务另含 `# TODO`）。
- `split_tests_for_hiding` —— 保留 1 个冒烟测试可见，其余测试函数移入 `hidden_tests`
  （装饰器与紧贴的注释随测试一起移动，避免留下悬空 `@parametrize`）。
- `protect_visible_tests` / `use_whole_suite_command`。

**标签描述的是产物，不是请求**：请求 T5 但材料只够 T3 会落到 T3；请求 T2 而材料够 T3 会升到 T3。
无任何层成立时返回空并由调用方丢弃。

题面机制泄露由 `find_disclosures` 检测（中英双语，对「当前实现用了 X 而不是 Y」这类**描述现有
实现**的句式判为泄露；对「应该返回 X」这类**规格陈述**不判）。检出后 `generate-cases` 会做一次
「只保留现象」的改写，仍失败则降级。

### 13.5.5 经验校准

结构不变量保证用例「看起来该有区分度」，只有跑起来才知道有没有。
`aibench calibrate-cases` 用锚点面板（`configs/runs/anchor-panel.yaml`）跑 `anchors × repeats` 次，
按 case 统计：

| 指标 | 含义 | 淘汰规则（默认） |
|------|------|------------------|
| `p_hat` | 全部尝试的通过率 | `> 0.9` 送分题；`< 0.05` 无人能过（多半是坏题） |
| `spread` | 最强锚点通过率 − 最弱锚点通过率 | 越大越能分离配置 |
| `point_biserial` | 该 case 结果与总体能力的相关 | `< 0.15` 判为噪声题 |
| `flaky` | 同一锚点多次重复结果不一致 | 标记，供人工复核 |

`anchor_fingerprint` 计入每个引用配置文件的**内容**而非路径：改了 `glm52.yaml` 里的 model 字段，路径全都没变但锚点含义已变，旧的 `p_hat` 必须整体失效，不能继续沿用。

锚点面板**必须跨越**要区分的能力带（至少一个弱锚、一个强锚，且同时变化模型与 agent 两条轴），
否则 `spread` 恒为 0，会把所有用例判为无区分度。

`aibench select-cases` 按 `spread`、`point_biserial` 降序把保留的 case 复制成新集合，并把
`metadata.calibration` 写回。

---

## 13.6 采样扩展（pass@k）与成本轴

实现：`src/aibench/runner.py`（`_run_one_case` / `_aggregate_attempts`）、
`src/aibench/report.py`（`_scaling_metrics`）、`src/aibench/stats.py`（`cost_curve`）。

### 13.6.1 三个必须分开的量

单次采样时 `pass@1`、`pass@k`、成功率三者恒等，因此**单次采样的 run 说明不了任何采样扩展收益**。
`max_attempts > 1` 后：

| 指标 | 定义 | 回答什么问题 |
|------|------|--------------|
| `pass_at_1` | 每 case 在 k 次中的通过比例，再对 case 求均值 | 单次抽样的期望结果 = 模型+Agent 原始能力 |
| `pass_at_k` | 至少一次成功的 case 比例（= `oracle_success_rate`） | 重复采样暴露出的**上限** |
| `pass_pow_k` | k 次全部成功的 case 比例 | 稳定性 |
| `success_rate` | **选择策略实际提交的**结果 | 主指标口径不变 |
| `selection_hit_rate` | 有解可选时策略选中成功解的比例 | 上限中有多少被策略吃到 |

`pass@k − pass@1` 是采样暴露的空间，`成功率 − pass@1` 是策略实际拿到的部分。

### 13.6.2 落盘布局

`results.jsonl` 仍是**一 case 一行**（`ablation.paired_outcomes`、`calibrate.aggregate_calibration`、
`report.build_summary` 都依赖这一点），新增 `attempts[]` 明细与聚合字段。
`total_tokens` / `wall_time_s` / `step_count` 等按 k 次**求和** —— 跑 k 次就是花了 k 次的预算。
`k=1` 时行为与单次采样逐字节一致。

`k > 1` 时每次尝试落在 `cases/<case_id>/attempt-<n>/`，聚合行写在 `cases/<case_id>/result.json`。

### 13.6.3 选择策略

| 值 | 含义 |
|----|------|
| `first-submit`（默认） | 提交第 1 次尝试 |
| `best-of-k` | 提交第一个通过的尝试 |

两者都会**跳过 infra_error 的尝试**：没跑起来的尝试不算一次提交，否则聚合行会带着基础设施失败的
`grade` / `failure_category`，却把自己报告成正常结果。

### 13.6.4 温度不为 0 是前提

`temperature: 0` 下 k 次采样是同一个样本，`pass@k ≡ pass@1`，指标看起来正常但恒为零收益。
`max_attempts > 1` 且温度为 0 时，runner 会在 stdout 告警并在 `run_manifest.json` 写入
`sampling_warning`。生产采样配置：`configs/models/glm52-sampling.yaml`（temperature 0.7）+
`configs/runs/passk.yaml`。

### 13.6.5 成本轴

`cost_curve` = 若干 token 预算档位上「在该预算内解出的 case 数 / 有效 case 数」。
档位由该 run 的 per-case token 分布分位数产生（`budget_quantiles`），不是人工拍板。

**跨配置比较时必须用同一组档位**，否则每条曲线各有各的 x 轴，无法横向对比。
消融报告另给 `token_amplification`（相对基线的 token 倍数）：准确率提升若是用 5 倍 token 买来的，
和等成本下的同等提升不是同一个结论。

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
| Stub 必须失败（下界） | `stub_fail_gate` | error | 是 | 见 §14.3.1 |
| 参考解必须通过（上界） | `solvability_gate` | error | 是 | 见 §14.3.3 |
| 分层不变量 | `tier_<violation>` | error | 是 | §13.5.2，逐条来自 `check_tier_invariants` |
| 干扰文件自相矛盾 | `tier_distractor_in_solution` | error | 是 | 声明 `role=distractor` 却被参考解改动 |
| 参考解无改动 | `tier_solution_file_unchanged` | error | 是 | 某个 gold file 与初始文件完全相同，是凑数项 |
| 参考解疑似重写 | `tier_solution_rewrites_file` | warn | 否 | 改动行占比 > 60%，无法定位缺陷 |
| Gold 污染 | `contamination_gold_in_context` | error | 是 | gold 全文已在 context |
| Key line 污染 | `contamination_keyline_in_context` | error | 是 | gold 模式下关键行已在 context |
| Prompt 过短 | `prompt_too_short` | error | 是 | `len(strip)<20` |
| Prompt 大代码块 | `prompt_contains_large_code_fence` | warn | 否 | 疑似泄漏，人工看 |
| 弱 grader 标记 | `weak_grader_flag` | warn | 否 | script 却标 weak_grader |
| 未定级 | `tier_missing` | warn | 否 | `metadata.tier` 缺失 |
| 重复指纹 | `duplicate_fingerprint` | warn | 否 | 集内 fingerprint 冲突 |
| 难度 | （写入 checks/metadata） | 注解 | — | easy/medium/hard（旧口径，保留兼容） |
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

#### 14.3.3 Reference-solution（可解性上界）

```text
若 grader.mode != "script":
    → 跳过（无命令可跑）

若无 gold_files:
    → 失败 no_reference_solution   # 没有参考解就无法验证可解性

否则:
    materialize_workspace(case)          # 初始现场
    覆盖 grader.gold_files 到工作区        # 参考解
    grade_case(...)                      # 注入隐藏测试后判分
    必须 passed，否则 solvability_gate 失败
```

**意图**：与 stub-fail 成对，构成难度的上下界。没有它，一个写坏的隐藏测试会让所有配置都失败，
在报告里看起来像「难题」，实际是**坏题**。

**实测证据（59 条集合，3 锚点 × 2 重复）**：18 条无人能解的用例里 **16 条没有参考解**；
而带参考解的 31 条里只有 2 条无人能解。因此「没有参考解就跳过检查」这一条豁免，
正是那 16 条坏题得以出厂的原因 —— 现在改为**没有参考解即判失败**。
代价是 41 条实测可解的用例里有 11 条因缺参考解被一并拒掉；正确的修法在上游：
让生成器始终产出参考解（T3 brief 已要求，59 条里 32 条做到了）。

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

### 17.4 配对显著性检验（McNemar）

`ablation_report.md` 除综述表外，还输出**分层成功率（按 tier）**与**配对显著性检验**：

| 列 | 含义 |
|----|------|
| `b` | 仅基线通过的 case 数 |
| `c` | 仅候选通过的 case 数 |
| 不一致数 | `b + c`，即两者结论不同的 case 数 |
| p 值 | 精确二项双侧检验；`p < 0.05` 判显著 |

**为什么不看各自的 Wilson CI**：两组跑的是同一个 case 集，属于配对数据。各自算独立区间会
丢掉配对信息 —— 例如 56/64 与 62/64 的 Wilson 区间大幅重叠、看起来「无差异」，而配对检验
在 6 个不一致 case 上给出 `p≈0.03`，判定显著。两者都通过或都失败的 case 不携带「谁更强」的
信息，配对检验按构造把它们排除，灵敏度因此高得多。

`ablation_summary.json` 对应字段：`pairwise_comparisons`、`tier_matrix`。

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
