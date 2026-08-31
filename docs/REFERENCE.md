# AI-Coding-Assist Benchmark — 项目参考手册（Reference）

> **`runs/` 下的路径是本地产物，未随仓库分发。** `/runs/` 在 `.gitignore` 里，
> `git ls-files runs` 返回 0 —— 本文引用的每一个 `runs/<name>_<timestamp>/` 目录，
> 在一个 clone 里都**不存在**。它们是原始机器上的证据指针，不是读者可以打开的路径。
> 需要其中的数字，请看 `benchmarks/ai_coding/calibrations/`（已入库），
> 或向 `CODEOWNERS` 里的所有者索取。


> **注意**：[`docs/html/reference.html`](html/reference.html) 是**另一份文档**，不是本文的展示版。
> 本文讲 CLI 与配置的参数级细节；那边讲设计论证、数据格式与门禁规则。  
> 文档站首页：[docs/html/index.html](html/index.html) · 项目介绍：[项目介绍 overview.html](html/overview.html)

| 项 | 值 |
|----|-----|
| 项目 | `aibench` / agent-scaling-benchmark |
| 版本 | 与 `pyproject.toml` 中 `version` 一致（当前 **0.1.0**） |
| 入口 | `uv run python -m aibench` · 安装后 `aibench` |
| 读者 | 使用者、评测工程师、对接 Agentic Scaling 汇报的同学 |
| 演示页 | [`docs/html/overview.html`](html/overview.html) |
| 结果表设计源 | [`docs/html/reference.html`](html/reference.html)；字段字典源 `docs/html/_src/tables.md`（未渲染成页面） |
| 本文 HTML | 无。`docs/html/reference.html` 是另写的一页，不是本文的渲染结果 |

本文是 **可执行系统** 的权威参考：概念、架构、Case 协议、配置、CLI、脚本、产物、设计表映射、操作规程与故障排查。  
快速上手见 [用户手册 HTML](html/manual.html)（由 `docs/html/_src/manual.html` 手写构建，与 `USER_GUIDE.md` 无关）；设计决策见 `docs/design/*`。运行时以 `aibench <cmd> -h` 与仓库现行 `configs/` 为准。  
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
| `src/aibench/agents/` | `mock` / `openai_compat` / `tool_loop` / `shell` / `bare_model` / `opencode`（六个，§12 逐个讲）|
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
  --limit 100 --max-cases 30 --require-gold --require-edits
  # --require-edits 是 --reverse 的前提：没有 metadata.file_versions 就没有 pre/post 对，
  # 下一步会一条也建不出来。scripts/e2e_pipeline.sh 两个都传，这里跟它一致。

uv run python -m aibench filter-drafts \
  --input-dir benchmarks/ai_coding/cases/drafts-from-db \
  --output-dir benchmarks/ai_coding/cases/drafts-kept \
  --dropped-dir benchmarks/ai_coding/cases/drafts-dropped \
  --report benchmarks/ai_coding/filter_report.json

uv run python -m aibench generate-cases \
  --input-dir benchmarks/ai_coding/cases/drafts-kept \
  --output-dir benchmarks/ai_coding/cases/auto-v0 \
  --reverse --resume --max-cases 8 --audit --secrets-scan

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
| Python | **3.13**（`.python-version` 固定）。`pyproject.toml` 的 `requires-python` 写的是 `>=3.11`，那是**安装**下限；`aibench doctor` 要求与 `.python-version` **完全相等**，因为 `uv.lock` 在 3.12 前后解析出不同的 numpy。3.11/3.12 上依赖安装与 `pytest` 都能过，`doctor` 会红 |
| 包管理 | 推荐 `uv` |
| 可选 | MySQL 可达（抽库）、OpenAI 兼容 API（生成与真 Agent） |

### 5.2 安装

```bash
cd agent-scaling-benchmark
uv sync --extra dev --extra grading
./scripts/install-hooks.sh   # pre-commit: ruff format + import + lint + secrets
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
| `AIBENCH_CASE_RETRY` | `2` | 可选 | case 因 **infra_error** 整 case 重跑次数。也可写进 run YAML 的 `case_retry:`（**优先**），这样它会落进 manifest —— 只走环境变量时，一次跑测重试了几次在任何产物里都看不出来 |
| `AIBENCH_ALLOW_UNSANDBOXED` | 无 | 可选 | **Linux 上跑 opencode 必读**。沙箱靠 `sandbox-exec`，那是 macOS 独有的；没有它，被测 agent 能读到本 case 自己的答案。默认拒跑并把每条 case 记成 `infra_error`。设为 `1` 表示知情照跑，产物里会记 `sandboxed=false`；或在 agent 配置里写 `options.sandbox: false`（同样是一次有记录的选择） |
| `AIBENCH_CASE_ROOT` | `benchmarks/ai_coding/cases` | 可选 | 生成用例集的根目录。指向别处即视为「刻意隔离」，`seed-v0` 这类与 fixture 同名的集合不再报冲突 |
| `AIBENCH_USD_PER_MTOK` | 无 | 可选 | 统一 $/百万 tokens 估算成本 |
| `AIBENCH_USD_PER_MTOK_INPUT` | `0.5` | 可选 | 分项时 input 单价 |
| `AIBENCH_USD_PER_MTOK_OUTPUT` | `1.5` | 可选 | 分项时 output 单价；未设 blended 时用 (in+out)/2 |

**禁止**将含真实密钥的 `.env` 提交到 git。

---

## 7. 生产配置体系 `configs/`

详见 [`configs/README.md`](../configs/README.md)。Mock **不**在此目录。

### 7.1 布局

`configs/` 下**全部 40 个 YAML**，由目录列表生成（此前这张表只列了 9 个）。写过 39 两次：第一次漏 `grading-env.yaml`，第二次改了数字没补行。
说明取自每个文件的第一行注释。

| 路径 | 用途 |
|------|------|
| `grading-env.yaml` | What the grading environment provides beyond each runtime's standard library. |
| `agents/bare_model.yaml` | The model with no scaffold between it and the grader. |
| `agents/bare_model_choice.yaml` | The same adapter as `bare_model.yaml`, pointed at a judgement instead of a repair. |
| `agents/bare_model_review.yaml` | The same adapter as `bare_model.yaml`, pointed at a judgement instead of a repair. |
| `agents/openai_compat.yaml` | Production: single-turn OpenAI-compatible coding agent |
| `agents/opencode-s1.yaml` | Anchor panel rung: opencode with a step budget of 1. |
| `agents/opencode-s10.yaml` | Anchor panel rung: opencode with a step budget of 10. |
| `agents/opencode-s3.yaml` | Anchor panel rung: opencode with a step budget of 3. |
| `agents/opencode-s40.yaml` | Anchor panel rung: opencode with a step budget of 40. |
| `agents/opencode.yaml` | Production: a real coding agent (opencode), driven as a subprocess. |
| `agents/shell.yaml` | Production template: wrap an external coding CLI (e.g. mini-swe-agent / opencode) |
| `agents/tool_loop.yaml` | Production: multi-step tool-loop agent |
| `agents/tool_loop_frugal.yaml` | Deliberately weakened multi-step agent: the floor anchor for retrieval tiers. |
| `models/deepseek-v4-flash.yaml` | Model slot for the model-only ablation. Same gateway, same credential. |
| `models/glm5.yaml` | Model slot for the model-only ablation. Same gateway, same credential. |
| `models/glm51.yaml` | Optional second model for multi-model ablation (same gateway) |
| `models/glm52-sampling.yaml` | GLM-5.2 with stochastic sampling, for pass@k / repeated-sampling experiments. |
| `models/glm52.yaml` | Production model: GLM-5.2 via OpenAI-compatible endpoint |
| `models/qwen37.yaml` | Optional model slot for multi-model ablation (set OPENAI_MODEL or change model:) |
| `runs/ablation-bare-models.yaml` | Model comparison with no scaffold, three repeats each. |
| `runs/ablation-choice.yaml` | Forced choice between two human-written patches, over `_choice2`. |
| `runs/ablation-hidden-discrimination.yaml` | Does the hidden-test dose buy DISCRIMINATION, or only difficulty? |
| `runs/ablation-hidden-dose.yaml` | Does withholding the grading signal move a real coding agent off the ceiling? |
| `runs/ablation-matrix.yaml` | Production ablation: real agents × models on session-derived auto-v0 |
| `runs/ablation-models-toolloop.yaml` | Model-only ablation, tool-loop scaffold. |
| `runs/ablation-models.yaml` | Model-only ablation: can this case set tell five models apart? |
| `runs/ablation-opencode-models.yaml` | Model comparison inside a real coding agent, three repeats each. |
| `runs/ablation-resolution-probe.yaml` | Does this case set have any resolution at all, or are the models simply equal? |
| `runs/ablation-review.yaml` | Patch review over `_review30`, against the widest capability gap the gateway offers. |
| `runs/ablation-two-models.yaml` | Two-model discrimination, repeated three times each. |
| `runs/ablation-widest-gap.yaml` | The widest capability gap the gateway offers, with no scaffold, three repeats each. |
| `runs/anchor-panel-opencode.yaml` | Calibration anchor panel that varies only the step budget. |
| `runs/anchor-panel-retrieval.yaml` | Anchor panel for retrieval tiers (T4+). |
| `runs/anchor-panel.yaml` | Calibration anchor panel: the reference points a case's difficulty is measured against. |
| `runs/baseline-bare.yaml` | Production single-run defaults for the bare-model adapter. |
| `runs/baseline-opencode.yaml` | Production single-run defaults for the opencode adapter. |
| `runs/baseline-tool-loop-frugal.yaml` | Floor anchor for retrieval calibration: multi-step, but a tight step budget and no shell. |
| `runs/baseline-tool-loop.yaml` | Production run: multi-step tool_loop agent + GLM-5.2 |
| `runs/baseline.yaml` | Production single-run defaults (no mock) |
| `runs/passk.yaml` | Repeated-sampling run: the budget axis is attempts, not steps. |

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
| `adapter` | string | 注册表键：`openai_compat` / `tool_loop` / `shell` / `mock` / `bare_model` / `opencode` |
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
| `--require-edits` | flag | 关 | 只取真正编辑过代码的会话（SQL 谓词 `full_history LIKE '%"name": "edit"%'`）。反向构造需要编辑前后两个版本，按时间抽样几乎取不到 |
| `--require-usable-pair` | flag | 关 | 只写出反向构造真能建题的草稿，判据就是 `generate-cases --reverse` 用的那条 `iter_file_versions`。**实测 `_rev_raw4` 3,312 条里只有 50 条合格（1.5%）**，2,977 条根本没有 pre/post 对，其余的 `pre` 无法证明来自一次完整 read。<br>⚠️ 默认关：本命令同时喂正向生成器，那条路线不需要 `file_versions`，默认开会把它**静默清空**。<br>⚠️ **不要照搬到既有草稿池上读数**：`_rev_raw4` 建于页脚修复之前，它里面的 Python 素材**全部**是无页脚、无从证明的那一类，所以按新判据 50 条里 **0 条是 Python**。要拿回 Python 素材必须重跑一次 `extract-from-db`（不花模型钱） |
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
| `--reverse` | flag | 关 | **主线开关。** 用反向构造：缺陷取自 trace 里真实的一次编辑（`stub = pre`、`gold = post`），模型只写测试和症状式 prompt。不加此参数走的是**正向生成**——让模型自己编题——那条路线已被三次干预实验判定失效（README §「已被否决的路线」），代码保留只为回归对照 |
| `--resume` | flag | 关 | 续跑：读 `_journal.jsonl`，跳过已写出的草稿。**长跑必带**——不带则中断后重跑会为同一批草稿二次付费 |
| `--no-deduplicate` | flag | 关 | 关掉按 `solution_key`（stub + 参考解的哈希）去重。默认开：`_rev2026` 的 134 条实测只有 66 组不同的 (pre, post)，51% 冗余，而重复会同时虚增 n 和让配对结果相关 |
| `--heuristic-only` | flag | 关 | 不调 LLM，仅 `heuristic_case_from_draft`。产物的 `metadata.generator.model` 记 `heuristic`，不记网关模型名 |
| `--max-cases` | int | `50` | 成功**写出**的最大条数 |
| `--oversample` | float | `1.5` | 每想要 1 条用例尝试多少条草稿。约 1/4 草稿会在下游被跳过（无参考解等），故需超采样；**每多一条草稿就是一次付费生成**。开跑前会打印「本次将从 N 条草稿生成」。<br>⚠️ 此前该系数写死为 `3` 且不可见：`--max-cases 600` 配 810 条草稿，会为**全部 810 次**生成付费而只写 600 条 |
| `--filter` | flag | 关 | 生成前再跑 `rule_filter_draft`，不 keep 则跳过 |
| `--workers` | int | `1` | 并行生成；尝试条数由 `--max-cases × --oversample` 决定 |
| `--secrets-scan` | flag | 关 | 生成后 `scan_case_dir`，写 `_secrets_scan.json` |
| `--audit` | flag | 关 | 生成后对每条 `audit_case` + `annotate` metadata |
| `--tier` | `T1..T5` | 草稿 `metadata.tier` | 强制目标层；不传则用 trace 推导的层（§13.5.3） |
| `--min-tier` | `T1..T5` | 无 | 定级低于此层的 case 直接丢弃 |

**行为细节**：默认 LLM 最多尝试 2 次，失败则 print fallback 并启发式；schema 非法则 skip；最终 `n_ok==0` 时 exit 1。元数据常含 `generation=llm|heuristic`、`review_status=needs_review`。

按目标层选用不同的生成 brief（`_TIER_BRIEFS`），产物再经 `settle_tier` 消毒定级，
`metadata` 记录 `tier` / `tier_requested` / `tier_notes` / `capability_axes` / `tier_facts`。
命令结束会打印实际定级分布，例如 `tier distribution: T2=31, T3=12`，以及缺陷机制分布
`problem_type distribution: missing_symbol=12, off_by_one=3, other=2`（见 `classify-cases`）。

> **case_id 重名会丢用例。** 文件名就是 `case_id`，所以生成器对不同草稿产出同一个 id 时，
> 后写的会覆盖先写的。实测一次 600 条的构建报告 `generated 600 cases`、磁盘上只有 575 个文件，
> 23 个 id 重复、32 次写入被吞掉。现在保留先到的那条、跳过后到的，并在结尾打印冲突数量与 id，
> 且 `generated N cases` 这个数**保证等于磁盘文件数**。

### 8.8 `ablation` — 矩阵消融

**作用**：同一 case set 上按矩阵跑多组 Agent/模型，汇总对比表。

| 参数 | 类型 | 默认 | 作用说明 |
|------|------|------|----------|
| `--matrix` | Path | 必填 | 矩阵 YAML（`runs[]`） |
| `--output-root` | Path | `runs/` | 生成 `ablation_<timestamp>/` |
| `--case-set` | str | 矩阵 `case_set` | CLI 覆盖矩阵默认集 |
| `--allow-weak-grader` | flag | 关 | 默认 **剥离** `metadata.weak_grader=true` 的 case 再跑 |
| `--parallel` | int | `1` | 矩阵行（实验）并行度。各行的 case 集会在开跑前一次性过滤好，不在 worker 里做 —— 否则两行共用同一集合时会互相 rmtree，输家跑在半拷贝的集合上 |
| `--baseline-experiment` | str | 矩阵字段或无 | 用于计算「相对基线收益」百分点 |
| `--export-csv` | flag | 关 | 写 `ablation_overview.csv` |
| `--export-xlsx` | flag | 关 | 写 xlsx（依赖 openpyxl） |

### 8.9 `export-bundle` — 导出可共享用例包（仓库之外）

用例**不入库**。要把用例交给别人复现，用这条路径导出到仓库之外的任意目录，再走你自己的共享渠道。

```bash
uv run python -m aibench export-bundle --from-set <set> \
  --output-dir /path/outside/repo/bundle \
  --drafts-dir benchmarks/ai_coding/cases/<该集合的草稿目录>
```

| 参数 | 类型 | 默认 | 作用说明 |
|------|------|------|----------|
| `--from-set` | str | 必填 | 源 case set |
| `--output-dir` | Path | 必填 | 任意路径，**刻意不是 case-set 名** —— 包要离开仓库，走 case-set 命名空间等于把生产代码放在离 git 历史一个 `git add` 的地方 |
| `--drafts-dir` | Path | 无 | 该集合所源自的私有草稿目录，用于逐行重合检查。**不传会告警**（stderr），因为那等于没做这项检查 |
| `--max-verbatim` | float | `0.05` | 逐行重合率上限 |
| `--no-require-audit` | flag | 关 | 允许导出审计未通过的用例（默认必须通过） |
| `--dry-run` | flag | 关 | 只出清单不写文件 |
| `--allow-production-derived` | flag | 关 | 允许导出 `metadata.generation == "reverse"` 的用例。反向路径逐字复制草稿，因此这是**交付生产衍生代码**的决定，不是格式问题 |
| `--allow-secrets` | flag | 关 | 允许导出 secrets 扫描报警的用例。2026-08-17 新增：五道门里唯有它此前没有任何出口，而同一道门在 `promote` 一直有 `--allow-secrets` —— 结果是愿意交付的人交出了一个更小、成分不同的集合，而所有已发布的 N 都是按另一个集合算的。用了会记进 `MANIFEST` |
| `--allow-review-choice` | flag | 关 | 允许导出 `metadata.generation == "review-choice"` 的用例。不加则 `_choice2` 的 167 条全部以 `provenance` 被拒 |

**五道门禁，全部机器判定，任一不过即排除**：schema、`metadata.validity_ok`、secrets 扫描干净、
来源（`metadata.generation`）、逐行重合 ≤ 阈值。

**没有一键 `--force`**，但**不等于没有出口**。每道门各有各的开关，都默认关、都写进 `MANIFEST`：
`--no-require-audit`（审计）、`--allow-secrets`（密钥）、`--allow-production-derived` 与
`--allow-review-choice`（来源）、`--max-verbatim`（重合）。这样设计是因为一个能一键放行全部门禁的
开关迟早会在赶时间时被用掉，而**逐门开关**要求使用者说清楚放行的是哪一样、并留下痕迹。

来源门放行的是 `llm` 与 `reverse`（后者需 `--allow-production-derived`），加
`--allow-review-choice` 后再 admit `review-choice`；本文此前写的
「`metadata.generation == "llm"`」只是其中一种。

**为什么来源必须机器判**（实测 `_scaleprobe` 575 条 vs 其草稿）：

| 生成路径 | 条数 | 实质代码行 | 逐行重合 |
|---|---:|---:|---:|
| `llm` | 541 | 8,156 | **1.7%**（全是样板） |
| `heuristic` | 34 | 11,777 | **100%** —— `heuristic_case_from_draft` 是深拷贝草稿 |

两类混在同一个目录里，**肉眼分不出来**；那 34 条逐字包含生产代码与内部路径。

**为什么重合检查是独立的第二道门**：LLM 那 541 条的重合率中位数是 0，但 p99 是 0.167，
**36 条超过 5%**。只看来源会把这批放行 —— 实测导出时正是 33 条因重合被拒。

产物含 `MANIFEST.json`：各门禁的通过/拒绝计数、**每条被拒用例的 id 与原因**、所用阈值、
tier 分布、case_id 清单。收到包的人据此可自行核对筛选过程，不必选择相信我们（§5.1）。

### 8.10 `promote` — 人工门控发布

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

### 8.11 `audit-cases` — 科学效度审计

**作用**：对 case set 跑 §14 全部门禁，输出汇总；可选回写 metadata。

| 参数 | 类型 | 默认 | 作用说明 |
|------|------|------|----------|
| `--case-set` | str | 必填 | 目标集 |
| `--report` | Path | 无 | 完整审计 JSON（含每 case issues） |
| `--annotate` | flag | 关 | 写入 `difficulty`、`tier`、`fingerprint`、`validity_ok`、`validity_issues` |
| `--llm-disclosure-check` | flag | 关 | 对非 T1 用例补一次 LLM 泄露二审（每条一次调用）。正则仍是唯一阻断判据，LLM 只能加 warn |
| `--fail-on-error` | flag | 关 | `failed>0` 时 exit **2**（便于 CI） |

报告含 `tier_distribution`；每条 case 的 `checks` 含 `stub_fail`、`reference_solution`、`tier`。

### 8.12 `compose-cases` — 合成 T4 检索用例

**作用**：把已验证用例的实现文件植入另一条已验证用例作为干扰文件，凑齐 T4 的广度要求。

| 参数 | 类型 | 默认 | 作用说明 |
|------|------|------|----------|
| `--from-set` | str | 必填 | 已通过审计的源集合 |
| `--to-set` | str | 必填 | 输出集合 |
| `--target-files` | int | `6` | 每条合成用例的文件总数 |
| `--donors-per-case` | int | `3` | 每条用例取几条其他用例作为干扰来源 |
| `--donor-set` | str | 同 `--from-set` | 干扰文件的来源集合。**宿主应取校准保留的集合，供体只需是合理代码**——两者都从筛后的小集合取会把合成饿死（实测 28 条宿主只合成出 3 条，且 0 条到 T4；改从未筛的 126 条取供体后：27 条合成、25 条 T4） |
| `--max-cases` | int | 无 | 上限 |

**两道门禁由构造保持**：宿主的 stub、测试与参考解一字未改，只新增与解无关的文件；干扰文件放在 `vendor/<donor_id>/` 子目录，不会 shadow 宿主的导入，且都不是测试文件，pytest / node 都不会收集。捐赠方的**测试文件被排除**——它会被运行器收集并跑起来。

捐赠方**自己的 stub 会被捐赠**：「是否属于解」是相对**宿主**而言的性质。B 的 stub 放进 A 的 `vendor/B/` 后，A 的导入路径够不到它，它不可能参与 A 的修复，正是合理但无关的代码。反过来排除它的代价实测过：一条典型用例只有 1 个实现文件，而那个文件恰是它自己的解要改的，于是 **126 条里有 109 条无任何可捐赠文件**。

捐赠者按固定顺序轮转而非随机：集合内容每次都变的话，就无法和之前的校准结果对比。

### 8.13 `calibrate-cases` — 经验校准（区分度实测）

**作用**：用锚点面板跑 `anchors × repeats` 次，按 case 统计 `p_hat` / `spread` /
`point_biserial` / `flaky`，给出 keep/drop 判定。见 §13.5.5。

| 参数 | 类型 | 默认 | 作用说明 |
|------|------|------|----------|
| `--case-set` | str | 必填 | 待校准集合 |
| `--anchors` | Path | `configs/runs/anchor-panel.yaml` | `anchors:` 列表（name/agent_config/model_config/run_config） |
| `--repeats` | int | `3` | 每个锚点独立重复次数（用于识别 flaky） |
| `--output-root` | Path | `runs/` | 生成 `calibration_<timestamp>/` |
| `--workers` | int | run 配置 | 单个 pass 内同时跑的 case 数 |
| `--parallel` | int | `1` | 锚点 pass 并发数。对网关的实际并发 ≈ `--parallel × --workers`；本网关实测在 16 并发下延迟仍平（4.7s→5.0s），吞吐 0.21→2.74/s |
| `--p-max` | float | `0.9` | 高于此通过率判送分题 |
| `--p-min` | float | `0.05` | 低于此通过率判无人能过 |
| `--min-rpb` | float | `0.15` | 点二列相关低于此判噪声题 |
| `--reuse-from` | Path | 无 | 上一次的 `calibration.json`；case 内容与锚点面板**都**未变的沿用旧结果，只跑变更/新增的 |

产物：`calibration.json` + `calibration_report.md`。**成本 = 锚点数 × repeats 次全量跑测**，
按需预算。`kept_count==0` 时 exit 1。

### 8.14 `select-cases` — 按区分度选题

**作用**：把校准保留的 case 复制成新集合，按 `spread`、`point_biserial` 降序。

| 参数 | 类型 | 默认 | 作用说明 |
|------|------|------|----------|
| `--calibration` | Path | 必填 | `calibration.json` |
| `--from-set` | str | 必填 | 源集合 |
| `--to-set` | str | 必填 | 目标集合 |
| `--max-cases` | int | 无 | 只取区分度最高的前 N 条 |
| `--tier-quota` | str | 无 | 各层占比，如 `T2=0.3,T3=0.4,T4=0.3`。不传则纯按区分度排序，可能整批落在同一层，把单一能力带当成全貌 |
| `--difficulty-quota` | str | 无 | 按**实测 p_hat** 的难度带配额组集，如 `easy=0.15,mid=0.70,hard=0.15`。带界：hard <0.2 / mid 0.2–0.8 / easy >0.8。份额须和为 1，带名拼错直接报错（否则会被当成池子不足，把人引去多跑校准）。与 `--tier-quota` 同时给出时，**难度带为外层**、tier 在带内分配。某带不够时**如实报欠填并返回偏少的集合**，绝不从相邻带补齐 |
| `--dry-run` | flag | 关 | 不写盘，只回报选择结果 |

写入时把 `metadata.calibration`（`p_hat` / `spread` / `point_biserial` / `attempts`）落到 case 上。

### 8.15 `secrets-scan`

**作用**：用正则扫描 case JSON 里**每一个字符串值**中疑似密钥。

| 参数 | 类型 | 默认 | 作用说明 |
|------|------|------|----------|
| `--case-set` | str | 无 | 与 `--input-dir` 二选一 |
| `--input-dir` | Path | 无 | 直接扫目录下 `*.json`（草稿目录同样可用） |
| `--report` | Path | 无 | 报告路径；stdout 也打印 |

**扫描范围是整份文档，不是字段清单。** 早先版本只读 prompt / context.files / gold_files /
hidden_tests 四个字段，而 `metadata.file_versions[].pre/post`（草稿里唯一未经消毒的 trace 原文）
不在其中 —— 三个真实 `sk-` 密钥、一个邮箱授权码由此进入草稿池而无人报告。
字段清单在新增字段时要同步修改，这一步被跳过过；遍历没有这一步。

**两处例外**，按位置而非按名字排除：`grader.key_lines` 与 `metadata.validity_issues`。
前者可能存着消毒器自己的 `sk-***` 占位符，后者是 runner 的原始输出且 `export-bundle`
写盘前就删掉 —— 报它们等于让门禁因自己产生的文本拒绝用例。
只按名字排除会连带豁免任何恰好用了这两个键名的子树，所以匹配的是完整路径。

**规则**（`secrets_scan.py`）：

| 类别 | 规则 |
|---|---|
| 固定格式 | `sk-…`、`sk-ant-…`、`AKIA/ASIA…`、PRIVATE KEY、`github_pat_…`、`gh[pousr]_…`、`glpat-…`、`xox[abpres]-…`、`AIza…`、JWT、带口令的数据库连接串 |
| 赋值形状 | `password`/`passwd`/`pwd` 与 `api_key`/`secret`/`token` 的赋值、`Bearer` |

固定格式类不走 `_is_code_not_credential` 的代码判别；赋值形状类走。
`clean=false` → exit 2。

> **已知精度问题（未修）**：赋值形状两条规则在生产源码上误报率高 ——
> `apiKey: config['X']`、`token = github_token`、`password: newPassword` 一类
> 「引用凭证的代码」被判成「写死的凭证」。`db_url_password` 在现有语料上
> **26 处命中全是误报**（README 的 `.env.example`、Rust 文档注释、测试断言）。
> 收紧它们需要一份对抗性召回语料先落盘，否则放宽会同时漏掉真凭证 —— 单独一轮处理。

### 8.16 `snapshot-skeleton`

| 参数 | 类型 | 默认 | 作用说明 |
|------|------|------|----------|
| `--case-set` | str | 必填 | 将 `context.files` 落到 `snapshots/<case_id>/`，并设 `workspace.mode=mixed` |

### 8.17 `export-ablation`

| 参数 | 类型 | 默认 | 作用说明 |
|------|------|------|----------|
| `--ablation-dir` | Path | 必填 | 已有消融目录 |
| `--csv` | flag | **True** | 导出 CSV |
| `--xlsx` | flag | 关 | 导出 XLSX |

### 8.18 `plan-sample-size` — 反推所需题量

配对检验只从「两个配置结论不同」的 case 学到东西，所以题量由**不一致率**和效应量共同决定，
不是拍脑袋定的。此前本节缺失 —— 一个自称参数级权威的文档漏掉了一整个子命令。

| 参数 | 类型 | 默认 | 作用说明 |
|------|------|------|----------|
| `--delta` | float | **必填** | 要检出的成功率差异，单位百分点（如 `10` 表示 10pp） |
| `--discordance` | float | 无 | 预期不一致率（%）。与 `--from-ablation` 二选一 |
| `--from-ablation` | Path | 无 | 从既有 `ablation_summary.json` 读实测不一致率 |
| `--alpha` | float | `0.05` | 显著性水平 |
| `--power` | float | `0.8` | 检验效能 |

```bash
uv run python -m aibench plan-sample-size --delta 10 --discordance 20
uv run python -m aibench plan-sample-size --delta 10 --from-ablation runs/ablation_<ts>/ablation_summary.json
```

### 8.19 `doctor` — 环境自检

把此前只写在注释里的外部版本要求变成退出码：python、node、`configs/grading-env.yaml` 的承诺、
`opencode` 版本、沙箱可用性。任一不满足退出 1。

| 参数 | 类型 | 默认 | 作用说明 |
|------|------|------|----------|
| `--json` | flag | 关 | 机器可读输出 |

### 8.20 `classify-cases` — 缺陷机制归类与分布

**作用**：按封闭词表给 case 打 `metadata.problem_type`（缺陷机制，不是 `task_type` 的动作类型），
并打印 / 写出分布。生成路径（LLM / heuristic / reverse）已经自动打标；本命令用于回填既有集合、
或只看分布不改文件。

词表：`missing_cli_wiring` · `off_by_one` · `wrong_predicate` · `missing_guard` ·
`missing_branch` · `normalize_transform` · `wrong_path_base` · `missing_field` ·
`missing_symbol` · `registry_omission` · `wrong_literal` · `other`。
主信号是 stub 与 `grader.gold_files` 的 diff；没有参考解时才回退到 prompt 关键词，否则为 `other`。
分类失败不会丢掉用例。

| 参数 | 类型 | 默认 | 作用说明 |
|------|------|------|----------|
| `--case-set` | str | 与 `--input-dir` 二选一 | 解析规则同 §8.0 |
| `--input-dir` | Path | 与 `--case-set` 二选一 | 直接扫一个 case 目录（`generate-cases --output-dir` 的产物） |
| `--annotate` | flag | 关 | 写回 `metadata.problem_type` / `_source` / `_reasons` |
| `--report` | Path | 无 | JSON：`total` / `counts` / `items` |

结束打印 `problem_type distribution: off_by_one=3, other=1`。无 case 时 exit 1。

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
| `--strict-audit` | 关 | 任一用例未过错误级门禁即中止。默认只写回 `validity_ok`，由 `ablation` 排除这些用例 |
| `--forward` | 关 | 用正向生成（模型自己编缺陷）替代默认的反向构造。**不是可选方案**：三次干预实验判定正向路线无效，保留它只为让那个否定结论可复现 |
| `--tier` | 空 | 强制每条草稿的目标层级 |
| `--min-tier` | 空 | 丢弃定级低于此的生成结果 |
| `--calibrate` | 关 | 跑 `calibrate-cases` + `select-cases` 再消融所选集合 |
| `--repeats` | `3` | 每锚点重复次数 |
| `--anchors` | `configs/runs/anchor-panel.yaml` | 锚点面板 |

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
| `scripts/lint.sh` | ruff `check --fix` + `format`。**这是修复工具，不是门禁** —— 它改写源码后再验证自己的输出，所以格式回归永远不会让它失败。门禁用 `ruff format --check` + `ruff check` |
| `scripts/install-hooks.sh` | 安装 pre-commit（ruff + 暂存文件的 secrets 扫描） |
| `scripts/check_doc_links.py` | 断链、失效标签、以及未声明为本地产物的 `runs/` 引用 |
| `scripts/build_docs_html.py` | 从 `docs/html/_src/*.html` 重建四页文档站 |
| `scripts/instrument_check.py` | 仪器自检。跳过的检查会列出并以 2 退出（INCOMPLETE），不再打印 PASS |
| `scripts/run_cost_report.py` | 汇总 `runs/` 下的 token / 调用次数 / agent 墙钟，并写明计价口径 |
| `scripts/discrimination_diagnostic.py` | 从既有消融读出「为什么这些用例谁都做不出」 |

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
| `command` | script 模式命令，如 `python -m pytest -q` / `node --test`。**运行时不过滤**，见 §14 |
| `gold_files` | gold 模式期望文件；对 T3+ 同时充当**参考解**（§14.3.2） |
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
| `problem_type` | 缺陷机制封闭词表（`missing_cli_wiring` … `other`）。**不是** `task_type`。生成时启发式打标，既有集合用 `classify-cases --annotate` 回填 |
| `problem_type_source` | 目前恒为 `heuristic` |
| `problem_type_reasons` | 命中的检测器说明，便于抽查 |

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
| `bare_model` | `configs/agents/bare_model.yaml` | 单次调用，返回整份修好的文件；测「模型本身有多强」 |
| `openai_compat` | `configs/agents/openai_compat.yaml` | 单轮 Chat；要求返回 `{"files":[...],"message":"..."}` 并写入 workspace |
| `opencode` | `configs/agents/opencode.yaml`、`opencode-s{1,3,10,40}.yaml` | 真实编码 agent（opencode CLI）作为子进程驱动；见 §12.4 |
| `tool_loop` | `configs/agents/tool_loop.yaml` | 自研多步工具：`list` / `read` / `write` / `bash` / `submit`。**已知会主导测量**，见 §12.4 开头 |
| `shell` | `configs/agents/shell.yaml` | 外部 CLI；占位符 `{workspace}` `{prompt_file}` `{case_id}` `{max_steps}`。不记 token，`empty_patch` 恒 false |
| `mock` | **仅** `tests/fixtures/configs/agents/mock.yaml` | 单测 / dry-run |

三种口径不要混用：`bare_model` 答「模型多强」，`opencode` 答「模型在真实 agent 里多强」，
`openai_compat` / `tool_loop` 答「模型在本项目自研脚手架里多强」。

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

**不要用它接 opencode。** 该示例已于 2026-08-17 从 `shell.yaml` 删除 —— 把本节警告过的
调用方式写成配置自带的示例，是最容易被照抄的位置。用 `configs/agents/opencode.yaml`：
它镜像工作区、记录 `sandboxed`、固定二进制版本，并上报真实 token 用量。

本适配器剩下的边界（两个已修，一个仍在）：

| 项 | 状态 |
|---|---|
| `(proc.stdout or "")[-2000]` 是单字符索引而非切片 | **已修**（2026-08-17）。它让任何输出不足 2000 字节的成功运行抛 `IndexError`，也就是这个适配器从来没有跑通过一次 —— 它此前没有任何测试 |
| 把工作区里**每个**文件都算作 written，`empty_patch` 恒 false | **已修**（2026-08-17）。现在比对运行前后的内容哈希，只报真正改动过的文件 |
| `usage` 恒为 0 | **仍在**。外部 CLI 不上报 token，本适配器也拿不到；因此成本轴的 `token_amplification` 对它无意义 |

### 12.4 `opencode` options

把 opencode CLI 作为子进程驱动。存在的理由：`tool_loop` 是本项目自研的脚手架，
与无脚手架口径对比时它**主导了测量而不是测量了对象**——同一批用例三次重复翻转 32.3%、
轮间极差 12.9pp（无脚手架 0.0pp），且修它内部三个缺陷让同一模型通过率移动 58 个百分点。
`opencode` 回答的是另一个问题：模型在人们真正使用的编码 agent 里表现如何。

| 选项 | 默认 | 说明 |
|------|------|------|
| `binary` | `opencode` | 可执行文件路径（实测版本 1.18.15） |
| `max_steps` | 取 run-config 的 `max_steps` | 钉死步数上限；阶梯锚点用它 |
| `system_prompt` | 见 `DEFAULT_SYSTEM` | 写进生成配置的 `agent.aibench.prompt` |
| `allowed_commands` | 同 `tool_loop` 的白名单 | bash 允许的程序名 |

**运行身份可复算**：provider、网关、模型、步数、工具集、权限全部由 `configs/models/*.yaml`
与 `.env` 在每次运行时生成到临时 `opencode.json`，经 `OPENCODE_CONFIG` 注入；
不读操作者的 `~/.config/opencode`。`--pure` 是必需的（否则加载插件时挂死，实测 150s 无事件）。

**边界由 `sandbox-exec` 承担，不是由 opencode 的权限配置承担。**
`external_directory: deny` **不是文件系统边界**：它只对硬编码的一小撮命令名做路径检查。
实测让 agent 读本仓库的一个文件——`cat` 被拒，而 `grep` / `head` / `sed` / `find`
**全部成功返回了文件内容**。（本项目早先版本声称该规则拦得住，依据是一个只用了
`cat` 与 `python3 -c` 的探针，而这两个恰好在被检查的集合里。这是 §5.9 的同型错误。）

现在的边界是 seatbelt 配置：`deny file-read-data file-write*` 覆盖仓库子树，
`allow file-read-data` 放行 `.venv`（grader 命令是 `python -m pytest -q`，
不放行 agent 就跑不了被评分的测试）。**只拒内容不拒元数据**——连 `file-read-metadata`
一起拒会让 opencode 在 `lstat` 上以 EPERM 退出，而 gold 解是文件里的字节，不是 stat。
同一个探针在加沙箱前泄漏、加沙箱后不泄漏，这是能区分改前改后的对照。
`artifacts.sandboxed` 如实记录本次是否真的施加了边界；无 `sandbox-exec` 的平台记 `false`，
不继承一个没得到的保证。opencode 的权限块保留为便宜的第二层。

**工作区镜像到仓库外**：opencode 向上走到 **git 根**来确定「项目目录」，
工作区留在 `runs/.../workspace` 时整个仓库都算「项目内」。镜像目标用
`tempfile.mkdtemp()` 并 `.resolve()`（macOS 上 `/var` 与 `/private/var` 不是一回事）。
跑完按内容双向镜像回原路径（含删除与符号链接），因为 grader 拿到的是原路径。

**权限里不要加 `read`/`edit` 路径规则。** `bash` 按原始命令串匹配，绝对路径模式有效；
`read`/`edit` 匹配不上绝对路径，于是 `{"*": "deny"}` 兜底会把 agent 锁在自己的工作区外——
实测每次 read 与 edit 全被拒、连两行缺陷都改不了，而**状态码仍是 `completed`、
单测全绿、数字上看不出任何异常**。

**子进程环境是重建的，不是继承的**：`OPENCODE_CONFIG` 是**追加**一份配置而非替换，
全局 `~/.config/opencode` 仍会被合并，故 `XDG_CONFIG_HOME` 一并指向 staging；
继承来的 `OPENCODE_*` 全部剔除（`OPENCODE_PERMISSION` 能整块覆盖权限）；
`PWD`/`OLDPWD` 也剔除（它们仍指向仓库，opencode 启动时会 stat 它）；
`OPENCODE_DB` 与 `XDG_DATA/CACHE/STATE_HOME` 每次运行独占——
共用单个 SQLite 并发跑会 `database is locked` 并让整行变成 `infra_error`。
`PATH` 故意保留，理由同上（`.venv/bin` 决定 agent 能否跑测试）。
子进程 `stdin=DEVNULL`（opencode 会把非 TTY 的 stdin 读到 EOF 并**追加进题面**）、
`cwd` 设为工作区、独立进程组（超时按组终止，否则它派生的 pytest/node 会在 staging
被删除时仍在写）。

**状态映射**（决定一行算模型的还是算基础设施的）：opencode 遇到网关错误会打一条
`{"type":"error"}` 并非零退出——只要出现该事件且退出码非零即 `infra_error`；
超时且零模型调用也是 `infra_error`（网关不通时的挂起与「预算耗尽」形状相同，
误判会把一次故障写成模型的失败并留在分母里且不重试）。

**残留风险**：seatbelt 是 macOS 专有且 Apple 已弃用；`artifacts.out_of_workspace_attempts`
记录越界尝试（含模型把路径写错这类假阳性，**不要读作作弊**）。
容器隔离仍是更彻底且可移植的答案，且能一并覆盖 grader 侧（§13 开头记的最大安全缺口）。

---

## 13. 判分（Grading）

| mode | 成功条件 |
|------|----------|
| `script` | 执行 `command` 且 exit code = 0 |
| `gold` | 文件内容按 `match` 与 gold / `key_lines` 匹配 |
| `llm_judge` | 模型评分 ≥ `judge_threshold` |
| `composite` | 优先 script，再回退 gold 等组合逻辑 |

**没有** grader 命令白名单：`grading.py` 用 `subprocess.run(shell=True)` 在宿主机执行 `grader.command`，无沙箱、无网络限制。（`generate-cases` 侧对**生成出来的**命令有白名单 `_SAFE_GRADER_CMD`，那是另一回事。）Docker/gVisor 隔离未做，是目前最大的安全缺口。  
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
| T4 | 上下文检索 | T3 + ≥5 文件 + ≥1 干扰文件 + ≥3 隐藏测试（参考解**仍只需 1 个文件**） | A1、A2 检索、A5、A6 |
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
| javascript / typescript | `*.test.{js,mjs,cjs,ts}` / `test.<ext>` / `test-*.<ext>`（**`*.spec.*` 不被 `node --test` 发现**） | `node --test`（内置，零依赖） | `ℹ pass N` / `# pass N` |

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

此外 `prompt_names_changed_function` 检测**题面是否点名了参考解要改的那个函数**。
它不是正则匹配题面，而是把参考解与 stub 逐行 diff，取每个改动块所在的函数名，
再看题面有没有提到 —— 因为「点名被修函数」把「找出缺陷」变成了「读这个函数」，
而定位能力正是 T1 以上各层要测的东西。

实测（`auto-v0` 有参考解的 105 条 × `runs/calibration_20260805_090754`）：

| 分组 | n | mean p_hat |
|---|---:|---:|
| 题面点名被修函数 | 56 | **0.905** |
| 未点名 | 49 | **0.704** |

**判为 `warn` 而非 `error`**：它命中约半数用例，直接拒绝会把题量腰斩。
它的作用是在 `generate-cases` 里触发那次「只保留现象」的改写；
审计里只作为告警出现，不阻断发布。

> 与之配套：`_delocalize_prompt` 的系统提示原本明文要求 "Keep the same function and file names"，
> 与本检测直接矛盾 —— 已改为「指称可观察行为，必要时可提入口或文件，但不得点名被改的函数」。

### 13.5.5 经验校准

结构不变量保证用例「看起来该有区分度」，只有跑起来才知道有没有。
`aibench calibrate-cases` 用锚点面板（`configs/runs/anchor-panel.yaml`）跑 `anchors × repeats` 次，
按 case 统计：

| 指标 | 含义 | 淘汰规则（默认） |
|------|------|------------------|
| `p_hat` | 全部尝试的通过率 | `> 0.9` 送分题；`< 0.05` 无人能过（多半是坏题） |
| `spread` | 最强锚点通过率 − 最弱锚点通过率 | 越大越能分离配置 |
| `point_biserial` | 该 case 结果与总体能力的相关，**已扣除本题对总分的贡献** | `< 0.15` 判为噪声题 |
| `incomplete_panel` | 该 case 未被全部锚点测到 | **阻断 keep**，但与上面三条分列 —— 说的是测量不完整，对策是重跑 |
| `flaky` | 同一锚点多次重复结果不一致 | 标记，供人工复核 |

`anchor_fingerprint` 计入每个引用配置文件的**内容**而非路径：改了 `glm52.yaml` 里的 model 字段，路径全都没变但锚点含义已变，旧的 `p_hat` 必须整体失效，不能继续沿用。

它**还计入 harness 源码摘要**（`agents/` + `grading.py` + `workspace.py` + `runner.py`，见
`provenance.harness_digest`）。只哈希三个 YAML 时，那次让同模型通过率变动 58pp 的适配器修复
前后指纹**完全相同**（都是 `5f5233c7214879f4`）—— 配置文件说的是「用哪个 agent 和模型」，
不说「怎么驱动它」。指纹带 `v2:` 前缀，理由与 `case_fingerprint` 的 `FINGERPRINT_VERSION` 相同：
**旧值与新值必须永远不可能相等**。代价是 14 份既有校准立刻不可复用，这是正确行为而非 bug。

#### 两个估计量缺陷（2026-08-11 修复）

**① `point_biserial` 曾把本题算进总分。** 纯噪声题的零分布因此不在 0，而在约 `1/√k`。
蒙特卡洛实测（9 次 run）：

| k | 旧式均值 | 修正后 |
|---:|---:|---:|
| 7 | **+0.377** | +0.011 |
| 31 | **+0.172** | −0.004 |
| 126 | +0.076 | −0.010 |

**k=31 时 +0.172 已高于 0.15 的阈值** —— 一道纯噪声题过「无区分度」筛选的概率超过一半。
修法是标准的 corrected item-total：先从总分里扣掉本题再算相关。

**③ 总分曾是通过「计数」，被掉行机械通缩。** 参考校准里各 run 的通过计数是
`[26,25,24, 7,5,6, 24,24,24]`，而各 run 实际产出的行数是 `[30,29,29, 9,9,9, 31,31,31]` ——
中间那个锚点看起来比最弱的还弱四倍，而它的通过**率**是 0.78/0.56/0.67，与其余相当。
「能力轴」大部分是「掉了多少行」轴。现在用**留一法通过率**（`item_rest_correlation`）：
既扣掉本题，又按该 run 实测的题数归一。实例 `rev-f4f8a7a78fa184cd` 因此从 0.470 变成 **0.035**。

> 留一必须在**同一量纲**里做。从一个比率里减去 0/1 的结果，会得到一个看似合理、
> 实则量纲错乱的数，而签名上看不出来 —— 所以 `item_rest_correlation` 收的是计数，
> 算术在函数内部完成。

**② 缺失的 run 曾按 0 计入 item 向量**，而 `p_hat` 只对存在的行求均值 —— 两个量在讲不同的样本。
缺失行同时压低 item 与该 run 的总分，制造出与「掉行」而非与能力的相关。
实例 `rev-4646d93ae250add0`：attempts 6/9、全部通过、`p_hat` 1.00，`r_pb` 却是 **0.996**。

对最近一次校准（`runs/calibration_20260809_231654`）从原始 `results.jsonl` 复算：
**keep 从 13 降到 3，另有 21 条判为 `incomplete_panel`。**
其中因估计量修正而改判的只有 1 条（面板完整的共 10 条）——
覆盖门与纠偏是两件独立的事，坍塌主要来自前者。
那 21 条不是坏题 —— 是面板没测完，对策是重跑。
`scripts/instrument_check.py` 会把这个数字打印出来（不设阈值，坍塌是信息）。

**锚点面板必须能施展被测层级的能力轴。** Agent 配置用 `capability_axes` 声明自己能施展哪些轴，
`calibrate-cases` 在开跑前核对该集合出现的所有 tier，不匹配直接拒绝（`--allow-unfit-anchors` 可强制）。

这条不是形式主义，是实测教训：单轮 agent 把**整个工作区贴进 prompt**，所以 T4 的干扰文件对它不是需要绕过的障碍，而是白送的可用代码 —— 在合成 T4 用例上，弱单轮锚点的通过率比同一批用例合成前**高了 24 个百分点**。一个施展不了某条轴的成员不会在那条轴上得低分，它得的是另一回事的分，跨这种面板算出来的 spread 不是那个能力的度量。

检索层（T4+）用 `configs/runs/anchor-panel-retrieval.yaml`：三个成员全是多步 agent，
差异落在真正支配检索的维度上 —— 步数预算、能否执行命令、读代码的模型强弱。
该面板**不能测 T5**（frugal 成员无 bash，缺 A3），核对会如实报出来。

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
| 参考解必须通过（上界） | `solvability_gate` | error | 是 | 见 §14.3.2 |
| 分层不变量 | `tier_<violation>` | error | 是 | §13.5.2，逐条来自 `check_tier_invariants` |
| 干扰文件自相矛盾 | `tier_distractor_in_solution` | error | 是 | 声明 `role=distractor` 却被参考解改动 |
| 参考解无改动 | `tier_solution_file_unchanged` | error | 是 | 某个 gold file 与初始文件完全相同，是凑数项 |
| 参考解疑似重写 | `tier_solution_rewrites_file` | warn | 否 | 改动行占比 > 60%，无法定位缺陷 |
| 题面点名被修函数 | `tier_prompt_names_changed_function` | warn | 否 | §13.5.4；实测 +20pp p_hat，命中约半数用例故不阻断，改为触发题面改写 |
| 工作区不可收集（stub） | `stub_fail_gate` + `checks.stub_fail.uncollectable` | error | 是 | §14.3.1；与「stub 如预期失败」分开计数 |
| 工作区不可收集（参考解） | `solvability_gate` + `checks.reference_solution.uncollectable` | error | 是 | §14.3.2；与「参考解真正失败」分开计数 |
| Gold 污染 | `contamination_gold_in_context` | error | 是 | gold 全文已在 context |
| Key line 污染 | `contamination_keyline_in_context` | error | 是 | gold 模式下关键行已在 context |
| 测试抄写源码 | `test_reads_source_text` | error | 是 | §14.3.7；测试 grep 实现的源码文本而非运行它 |
| 隐藏测试索要不可知符号 | `hidden_test_requires_unknowable_symbol` | error | 是 | §14.3.8；隐藏了行为可以，隐藏了接口不行 |
| 隐藏测试索要不可知**关键字参数** | `hidden_test_requires_unknowable_kwarg` | error | 是 | §14.3.8。上一条只看符号名，而 `f(x, mode="strict")` 里 `f` 可见、`mode=` 不可见 —— 同一个洞，参数那一侧。`docs/SESSION-2026-08-14.md` 与 `docs/HANDOFF.md` §0.2 曾写「本轮未修」，2026-08-17 已修 |
| Gold 是集合控制文件 | `gold_is_collection_control_file` | error | 是 | 参考解落在 `conftest.py` / `pytest.ini` / `setup.cfg` 之类文件上——改的是判分环境，不是缺陷 |
| 门禁没跑成 | `stub_fail_gate_unverified` / `solvability_gate_unverified` | **warn** | 否 | 这台机器判不了（缺 node、缺评分依赖）。**故意不是 error**：`audit-cases --annotate` 会把结论写死进 case 文件，把「本机跑不了」记成 `validity_ok: false` 等于替所有后来的读者污染语料 |
| 判分文件带工具页脚 | `case_contains_tool_output_footer` | error | 是 | §14.3.9；stub 可能因加载不了而「失败」，不是因为缺陷 |
| Prompt 过短 | `prompt_too_short` | error | 是 | `len(strip)<20` |
| Prompt 大代码块 | `prompt_contains_large_code_fence` | warn | 否 | 疑似泄漏，人工看 |
| 弱 grader 标记 | `weak_grader_flag` | warn | 否 | script 却标 weak_grader |
| 未定级 | `tier_missing` | warn | 否 | `metadata.tier` 缺失 |
| 重复指纹 | `duplicate_fingerprint` | warn | 否 | 集内 fingerprint 冲突 |
| 难度 | （写入 checks/metadata） | 注解 | — | easy/medium/hard（旧口径，保留兼容） |
| 指纹 | fingerprint / content_fingerprint | 注解 | — | 去重与复现 |

抽取端另有三条谓词，在**生成之前**拒绝素材，不产生 issue code（见 §14.3.7 末）：
`defect_is_not_semantic`（只改注释的编辑不是缺陷）、测试文件本身不作为被测实现、
`unsatisfiable_imports` 现在也能看见相对导入。反向构造路径同时会自动填 `grader.protected_paths`，
从而启用 `detect_grading_interference`。

### 14.3 门禁逻辑详解

#### 14.3.1 Stub-fail（script 题核心）

```text
若 grader.mode != "script" 或 command 为空:
    → 跳过，detail=skipped_non_script，视为通过

否则:
    tmp = 临时目录
    materialize_workspace(case)     # 仅初始现场，未经 Agent
    grade = grade_case(case, ws)    # subprocess.run(shell=True)，命令不过滤
    若 grade.infra_error:  失败（环境/命令问题）
    若 grade.passed:       失败 stub_passed_grader  # 初始已过测
    否则:                  通过 stub_failed_as_expected
```

**意图**：Agent 必须做出有效修改才能得分。

#### 14.3.2 Reference-solution（可解性上界）

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

#### 14.3.3 Contamination（答案污染）

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

#### 14.3.4 Prompt 过短

`len((prompt or "").strip()) < 20` → `error prompt_too_short`。

#### 14.3.5 难度启发式（不 fail）

```text
score = 文件数 + (路径含 test 的文件中 def test_ 个数) + (总行数 // 40)
score ≤ 4  → easy
score ≤ 12 → medium
else       → hard
```

用于报告分层，不是严格能力标定。

#### 14.3.6 指纹与集级去重

```text
case_fingerprint = "v3:" + sha256(json 规范化的 {
    version, task_type, language, prompt.strip(),
    files:   [[path, sha256(content)], ...]   # 按声明顺序，顺序参与哈希
    grader:  {mode, command, match, key_lines, protected_paths,
              judge_rubric, judge_threshold,
              gold_files:   [[path, sha256(content)], ...],
              hidden_tests: [[path, sha256(content)], ...]},
    workspace: {mode, spec}
})[:16]

content_fingerprint(set) = sha256(sorted "case_id:fp" 行)[:16]

同集内相同 fingerprint 的多条:
  → 各自 warn duplicate_fingerprint（列出重复 id 列表）
```

`content_fingerprint` 会写入 run 的 `run_manifest.json` / summary，便于复现核对「是否同一 case 集」。

> **难度带的分辨率取决于尝试次数。** p_hat 只能取 `k/(anchors × repeats)`。
> 3 锚点 × 2 重复 = 6 次时，实测取值就是 `{0, 1/6, 2/6, 3/6, 4/6, 5/6, 1}`；
> `SelectionPolicy` 丢掉 0 和 1 之后，**hard 带只剩 1/6 一个值、easy 带只剩 5/6 一个值**。
> 想要 15:70:15 这类分布真正有意义，必须先提高每条用例的尝试次数，
> 光靠多攒用例是凑不出来的。

**为什么带版本前缀（`v3:`）**：指纹的唯一消费者是 `calibrate-cases --reuse-from`
的复用判据。旧口径只哈希 `task_type|prompt|paths`，**改文件内容、改 `grader.command`
都不会让指纹变化**，于是复用会交还一份在另一份代码上测出来的 p_hat。
版本前缀让旧值与新值永远不可能相等，复用门禁据此直接拒绝并打印丢弃条数。

> **这是有意的破坏性变更**：升级后所有既有校准结果都不再可复用，`--reuse-from` 会全量重跑。
> 这是正确行为而非 bug —— 旧指纹本来就无法反映内容变化。

**不覆盖的部分**：`context.workspace` 为 `snapshot` / `git` / `mixed` 时，
指纹只包含 workspace **规格**，不包含快照或克隆的**内容**（`case_fingerprint` 不做 I/O，
也拿不到 case-set 目录）。这类用例由 `validity.external_workspace()` 标出，
`plan_calibration` 对它们**一律不复用**，而不是信任一个证明不了内容未变的指纹。

#### 14.3.7 测试抄写源码（`test_reads_source_text`）

反向构造的安全性论证（`reverse_case.py` 模块注释）是：模型写不出能区分 pre 与 post 的测试，
用例就会被 §14.3.1 与 §14.3.2 拒掉，所以模型**没有能力**把题变简单。

这个论证有一个洞：**源码文本天然能区分两版**，因为修复改动的就是文本。
一份 `assert.match(source, /async function prepareReview/)` 这样的测试，
在 stub 上必失败、在 gold 上必通过，**两道门禁按构造 100% 通过**，
而它评的是抄写，不是行为。实测 `_revmixed` 31 条里 **12 条**如此，
语种分布 **JavaScript 11/14、Python 1/17**。

判据是三条规则的并集（在真实集合上校准）：

| 规则 | 命中 | 独有命中 |
|------|-----:|---------:|
| 反射取源码（`inspect.getsource` / `getsourcelines`） | 1 | 1 |
| 含读调用的**那一行**里出现 impl 文件名（排除写模式） | 4 | 1 |
| 存在读调用**且**有字符串包含/正则断言（`.includes` / `.match` / `assertIn` / `in source`） | 10 | 7 |
| **并集** | **12** | — |

三条各自非冗余。第二条只看**含读调用的那一行**而非整个文件，是刻意的：
JS 测试必然在 import 行写出 impl 文件名，按全文匹配会退化成「任何读操作都算」，
同一集合上从 4 条涨到 11 条，把合法的数据 fixture 读取也抓进来。

单条宽正则（「测试里出现 `readFileSync` 或 `open`」）会额外误伤 6 条：3 条用 `open(..., 'w')`
写 fixture（靠 mode 实参排除），2 条用 `os.fdopen(fd, 'w')`、1 条调 `urlopen`
（这三条压根不匹配 —— `fdopen`/`urlopen` 里的 `open` 前面没有词边界）。

实参按**配对括号**取，不是 `[^)]*`：后者在第一个 `)` 就停，
`open(os.path.join(d, "o.txt"), "w")` 会丢掉 mode 实参而被当成读，
而 `os.path.join` 在测试里再普通不过。mode 也**逐个实参**匹配，不扫整个实参串 ——
扫整串时 `readFileSync(p, 'utf8').includes('a')` 里的 `'a'` 会被当成 append 模式，
于是整个读调用被跳过：把断言里的 `'foo'` 改成 `'a'` 就足以走过一道 error 级门禁。

`grader.hidden_tests` 与可见测试一起扫描 —— 抄写可以藏在求解者看不到的那一半里。

判据脚本 `scripts/t1_gate_report.py` 断言的是**命中集合**而非命中数：
一个既误伤又漏抓、数量恰好相等的检测器，从退出码上看不出区别。

**已知局限**：当被测行为**本身就是文件内容**时（文档生成器、配置写入工具），
规则 3 会误伤一个做得对的测试。根治要把规则 3 绑定到读取目标（解析到 case 自带的 impl
文件才触发），本轮未做。三个已发布集合 `auto-v0` / `disc-v0` / `retrieval-v0` 零命中，
但那 181 条里总共只有 1 处读调用，所以这是弱证据。

#### 抽取端的三条谓词（不产生 issue code）

| 谓词 | 位置 | 拒绝什么 | 实测 |
|------|------|----------|------|
| `defect_is_not_semantic` | `file_versions.py` | 剥离注释/docstring 后 pre == post | Python 池 4/91 |
| 测试文件排除 | `reverse_case.py` | `spec.is_test_path(path)` 为真 | Python 35/91、JS/TS 88/269 |
| 相对导入 | `unsatisfiable_imports` | `from .x import y` 此前被判为「可满足」 | −1 |

`defect_is_not_semantic` 的方向是**保守的**：不可解析的 Python、未知语言、未闭合的块注释、
未闭合的字符串，一律返回 False（保留素材）。漏拒一个坏候选比误拒一个真缺陷便宜。
JS 侧的模板字面量内容**逐字节比较**，只有它周围的代码走空白归一 ——
多行模板里的缩进是数据不是排版，归一掉会让「重排模板」读成「什么都没改」。

> ⚠️ `unsatisfiable_imports` 的判定**依赖工作目录**：`find_spec` 会解析隐式命名空间包，
> 仓库根在 `sys.path` 上时，`import src...` / `import tests...` 被本项目自己的目录满足。
> 同一批草稿存活数 24 vs 21，取决于调用方式。详见 `docs/HANDOFF.md` §0.7。

#### 14.3.8 隐藏测试索要不可知符号（`hidden_test_requires_unknowable_symbol`）

规则一句话：**隐藏行为可以，隐藏接口不行。** 隐藏测试向实现索要的符号，
若在解题者能读到的任何地方（shipped 文件、可见测试、题面）都不出现，判 error。

为什么需要它：隐藏测试是本项目**唯一**动过难度的杠杆，实测把真实编码 agent 从 19/19 压到 11/19。
但同样是「变难」，来源有两种。8 条变难的用例里 7 条挂在断言上 ——
考的是可见测试没钉住的行为，这正是目的；另有 2 条挂在
`module 'parse_reports' has no attribute 'create_shared_strings'` 这类错误上 ——
隐藏测试调用的函数名在实现、可见测试、题面里**都不存在**。

后者不是难题，是无解题：所有解题者以同一原因失败，因此**区分不了任何东西，
却在通过率上长得和难题一模一样** —— 本项目反复要从中挖出来的正是这个形状。

实现两处易错，都是第一版写错后由实测纠正的：

1. **参考解不计入「可见面」。** 缺失的符号**必然**出现在参考解里（那正是它能过可解性门禁的原因），
   而参考解恰恰是解题者读不到的。算进去会让真正的污染用例判为干净。
2. **文件名不是属性访问。** `postcss.config.js` 里的 `js`、`..._parallel.py` 里的 `py`、
   以及 `__file__`，第一版把它们当成被索要的符号，**报 4 个假阳性同时漏掉真阳性**。

误伤情况：

| case set | 用例 | 有隐藏测试 | 被检查符号 | 命中 |
|---|---:|---:|---:|---:|
| `auto-v0` | 126 | 92 | 102 | **0** |
| `disc-v0` | 28 | 25 | 26 | **0** |
| `retrieval-v0` | 27 | 26 | 27 | **0** |

三个已发布集合合计 155 个符号零命中。与 §14.3.7 那次「零误伤」不同，这里暴露面足够大，
证据是有分量的。

> **注意它挡不住的**：难度与区分度是两件事。同一批用例在 hidden=4 上，
> 两个模型一起从 100% 掉到 ~65%，而配对不一致只有 1/19，且那一条正是两个模型
> 各自对自己也会翻转的用例 —— 实测区分度为零。**这道门禁提高的是难度的成色，不是区分度。**
> 详见 `docs/HANDOFF.md` §0.0b。

#### 14.3.9 判分文件带工具页脚（`case_contains_tool_output_footer`）

规则一句话：**被判分的文件里不能有 read 工具对它自己输出的描述。**

反向构造用 trace 读到的内容重建文件，而 read 工具会在内容末尾追加一行自述。
把那行当成文件内容留下来，代价不是「多一行噪声」：

- `rev-d098848d56868e13` 发出去的 stub 末尾是 `(End of file - total 152 lines)`，
  `node --check` 报 `SyntaxError: Unexpected token 'of'` —— **它根本不是合法 JavaScript**。
  于是 `check_stub_fails` 是因为**文件加载不了**而通过的，不是因为有缺陷。
  删掉那一行后重跑该用例自己的 grader：5 个测试 1 过 4 挂，挂的是真的 `ERR_ASSERTION`。
- `rev-461e8d91390e3915` 的 stub 末尾是 `(Showing lines 1-40 of 190. Use offset=41 to continue.)`,
  它是一个 190 行文件的**前 40 行**，断在 `success: false,` 中间，而参考解是完整文件。
  这道题实际在问「把片段补成整文件」。

**范围绑定判分标的**：`role: impl` 的 `context.files`、`grader.gold_files`、
`grader.hidden_tests`、`grader.protected_paths`，路径按 `safe_relpath` 归一后比较。
**`role: distractor` 与 `role: spec` 不在范围内** —— 干扰文件本来就是噪声，
多一行不改变用例测的东西。这与 §14.3.7 记的「规则 3 该绑定到读取目标」是同一件事。

早先一版用「与某个 gold 文件同路径」做代理，有两个洞：
**22 条已发布用例根本没有 gold 文件**（`solvability_gate` 已判它们不合格），代理对它们完全失效；
而 `auto-v0/db-0cf7e420-…` 的 `protected_paths` 里那个可见测试带页脚，
它的三个 `role: impl` Python 文件**都不能 `ast.parse`**，该用例却标着 `validity_ok: true`。

实测命中 / 其中「此前未被判负」：

| 集合 | 命中 | 新增判负 |
|---|---:|---:|
| `_revclean` | 2 | 2 |
| `_revmixed` | 9 | 2 |
| `_rev6` | 61 | 12 |
| `auto-v0`（已发布） | 12 | **2** |
| `disc-v0`（已发布） | 1 | 0 |
| `retrieval-v0`（已发布） | **0** | 0 |
| `_scaleprobe` | 14 | 5 |

`retrieval-v0` 那 6 条命中页脚的文件全是 `role: distractor`，因此正确地不判负 —— 所以上面两张表里 `retrieval-v0` 的 **0** 指的是「因页脚被判负的用例数」，不是「含页脚的文件数」。两个数都对，是两件事。

匹配**行锚定** `^\(…\)$`：语料里有一个 `filesystem.py` 是 read 工具自身的实现，
源码里就含 `result += f"\n\n(Showing lines {offset}-…)"`，子串匹配会误伤它。

> **它什么时候才生效**：`calibrate.py` 不读 `validity_ok`，
> `export_bundle` 与 `compose` 读的是**落盘的旧值**，只有 `promote` 会重跑 `audit_case`。
> 所以这条门禁要到重跑一次 `audit-cases --annotate` 之后才对既有集合起作用。
>
> ⚠️ **而 `scripts/e2e_pipeline.sh:165` 会自动跑 `audit-cases --case-set auto-v0 --annotate`。**
> `annotate` 原地改 case 文件，而所有 case 集合都在 `.gitignore` 里、git 从未跟踪过 ——
> 旧判定无法从版本库恢复。**跑它之前先 `cp -a` 一份集合目录。**
> 下游 `compose.load_verified_cases` 会**静默**丢掉判负的用例（不打任何诊断），
> `export-bundle` 则会把理由记进 manifest。

#### 14.3.10 `annotate` 写回字段

| metadata 键 | 值 |
|-------------|-----|
| `difficulty` | easy/medium/hard |
| `fingerprint` | `v3:` + 16 位 hex |
| `validity_ok` | bool |
| `validity_issues` | issue 对象列表 |
| `uncollectable_stub` | bool，stub 工作区是否根本收集不起来 |
| `uncollectable_reference` | bool，参考解工作区是否根本收集不起来 |

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
| 分层成功率 | task_type / difficulty / tier / problem_type | 结构解读 |
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

- 实验标识：`run_id`、`experiment_name`、`experiment_time`
- **执行身份**（2026-08-11 新增，见下）：`code_version`、`harness_digest`、`dependency_digest`、
  `venv_digest`、`python_version`、`node_version`、`opencode_version`、`platform`、`gateway_base_url`
- Benchmark 口径：`benchmark_name`、`case_set`、`case_count`、`effective_case_count`、`grouping`
- 算法配置：预算轴/值、分支、attempts、steps、选择策略
- Agent 与模型：名称版本、主模型、组合摘要、采样参数
- 主质量：成功数/率、完成数/率、空 patch、infra 错误、Wilson CI
- 成本效率：总 Token、均值、估算 USD
- 时间效率：总墙钟、吞吐、平均耗时
- Agent 行为：总 steps、模型调用次数

#### 执行身份字段

`code_version` 此前是字面量 `aibench@0.1.0 / agent@1.0.0` —— 148 份 manifest 全都一样，
那次让同模型通过率变动 58pp 的适配器修复在任何产物里都没有痕迹。

| 字段 | 含义 |
|------|------|
| `code_version` | 运行时 `git rev-parse --short HEAD`，工作树脏时加 `-dirty`。仓库根与 `repo_root()` 不一致、或 git 不可用时为 `unknown-worktree`；无法判定干净与否时为 `<sha>-unknown-cleanliness` |
| `harness_digest` | 决定「一次跑测意味着什么」的源码摘要：`agents/` + `grading.py` + `workspace.py` + `runner.py` + `languages.py` + `retry.py` + `models.py` + `env_config.py` + `calibrate.py` + `stats.py`（**十项**；后两项 2026-08-17 补入 —— 它们决定一次校准意味着什么，漏掉会让 `--reuse-from` 把旧的点二列相关系数带过一次估计器变更）。也并入 `anchor_fingerprint` |
| `dependency_digest` | `uv.lock` 的哈希，依赖变动可见 |
| `python_version` / `node_version` / `opencode_version` | 实际运行时版本。`opencode_version` 记录脚手架本身——不同版本是不同的仪器 |
| `venv_digest` | 解释器所在虚拟环境的内容哈希，取代了原来的 `python_executable`：回答「是不是同一套依赖」而不写出本机路径 |
| `platform` | 操作系统与架构。macOS 有 `sandbox-exec`，Linux 没有，两边量的不是同一个边界 |
| `gateway_base_url` | 环境里解析到的网关地址。**API key 从不进入产物**，有测试锁住 |
| `grading_env_digest` | `configs/grading-env.yaml` 与它承诺的每个包的**已安装版本**的哈希。两次跑测因为一边 numpy 2.1、一边 2.3 而结论不同时，此前没有任何产物说得出来 |
| `grading_env_unsatisfied` | 该清单里本机导入不了的名字。非空意味着导入这些包的用例会在判分时失败，读起来像难度 |
| `case_retry` | 单条 case 因 `infra_error` 整体重跑的次数（run YAML 的 `case_retry` 优先于 `AIBENCH_CASE_RETRY`）|
| `expected_case_set_fingerprint` | run 配置声明它应当测的语料指纹；与实测不符即拒跑。`.` 开头的派生子集跳过该比较 |

> 2026-08-17 起 manifest 里**不再有本机路径**：`python_executable` 与 `working_directory` 已由
> `venv_digest` 与 `platform` 取代。历史 manifest 仍带 —— `runs/` 已 gitignore 且没有 manifest
> 入库，`export-bundle` 也不携带 manifest，但**手工分享一个旧 run 目录仍会带出去**。

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

`configs/runs/ablation-matrix.yaml` 有**四行**，每行只动一个轴：

| `experiment_name` | 相对基线动了什么 |
|---|---|
| `openai-compat-glm52` | **基线** |
| `openai-compat-glm51` | 模型轴：GLM-5.2 → GLM-5.1 |
| `tool-loop-glm52` | 适配器轴：`openai_compat` → `tool_loop` |
| `passk-glm52` | 采样轴：attempts 1 → k |

四行产生**三组**成对比较（`compare_runs_pairwise` 跳过基线自身）。
本节此前只写了前两行，并说「可选第二模型行已在 YAML 中注释」——那两行没有被注释掉。

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
| `docs/html/_src/agentic-scaling-benchmark.html` | 结果表与评测协议的**设计报告**。**孤儿文件** —— 页面已不再生成，内容未并入四页站 |
| `docs/html/_src/tables.md` | **字段字典**（列名、含义、类型）。**孤儿文件** —— `write_tables_page` 已不再被调用 |

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
│   │   ├── overview.html / manual.html
│   │   ├── reference.html / index.html
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
uv sync --extra dev --extra grading
cp .env.example .env && set -a && source .env && set +a
./scripts/install-hooks.sh

# 仅生成用例
uv run python -m aibench extract-from-db \
  --output-dir benchmarks/ai_coding/cases/drafts-from-db \
  --limit 100 --max-cases 30 --require-gold --require-edits
  # --require-edits 是 --reverse 的前提：没有 metadata.file_versions 就没有 pre/post 对，
  # 下一步会一条也建不出来。scripts/e2e_pipeline.sh 两个都传，这里跟它一致。
uv run python -m aibench filter-drafts \
  --input-dir benchmarks/ai_coding/cases/drafts-from-db \
  --output-dir benchmarks/ai_coding/cases/drafts-kept
uv run python -m aibench generate-cases \
  --input-dir benchmarks/ai_coding/cases/drafts-kept \
  --output-dir benchmarks/ai_coding/cases/auto-v0 \
  --reverse --resume --max-cases 8 --audit --secrets-scan
uv run python -m aibench validate-cases --case-set auto-v0
uv run python -m aibench classify-cases --case-set auto-v0 --annotate --report /tmp/pt.json

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
