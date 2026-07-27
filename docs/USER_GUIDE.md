# AI Coding Assist Benchmark — 用户使用手册

面向：从真实会话构建候选测评集、一键跑测、Agent/模型消融，并产出对齐 Agentic Scaling 结果表的报告。

---

## 1. 系统在做什么

```text
MySQL llm_chat_records（或 JSON 导出）
        │
        ▼
  extract-from-db / extract-cases     # 抽草稿 draft
        │
        ▼
  filter-drafts                       # 规则筛选
        │
        ▼
  generate-cases                      # 启发式或 LLM 生成可验收 case
        │
        ▼
  validate-cases → auto-v0（候选集）
        │
        ▼
  run / ablation                      # 单次或矩阵消融
        │
        ▼
  runs/.../summary.json + tables.json + ablation_report.md
```

| 名称 | 含义 |
|------|------|
| **draft** | 从会话抽出的草稿，未必要可跑 |
| **auto-v0** | 自动候选 case 集（`needs_review`，**非**人工发布正式集） |
| **published** | 人工审核后的正式集（需自行复制/改名，流水线不自动写入） |

---

## 2. 环境准备

```bash
cd agent-scaling-benchmark
uv sync
cp .env.example .env   # 按需填写
```

CLI 启动时会自动加载项目根目录 `.env`。也可手动：

```bash
set -a && source .env && set +a
```

### 2.1 环境变量

| 变量 | 默认 | 必填场景 | 作用 |
|------|------|----------|------|
| `AIBENCH_DB_URL` | 无 | 从 MySQL 抽取 | SQLAlchemy URL，例如 `mysql+pymysql://user:pass@host:3306/opencsitool_db?charset=utf8mb4` |
| `OPENAI_API_KEY` | 无 | 真模型 Agent / LLM 生成 case | Bearer Token；兼容 `AIBENCH_API_KEY` |
| `OPENAI_BASE_URL` | 无（Agent 侧无配置时退回 `https://api.openai.com/v1`） | 私有/中转 API | Chat Completions 根路径，需含 `/v1` 前缀习惯 |
| `OPENAI_MODEL` | 无（用 YAML 里的 `model`） | 建议设置 | 运行时覆盖模型配置中的模型名 |
| `AIBENCH_API_KEY` | 无 | 可选 | 与 `OPENAI_API_KEY` 二选一 |
| `AIBENCH_BASE_URL` | 无 | 可选 | 与 `OPENAI_BASE_URL` 同义兜底 |
| `AIBENCH_MODEL` | 无 | 可选 | 与 `OPENAI_MODEL` 同义兜底 |
| `DATABASE_URL` | 无 | 可选 | `AIBENCH_DB_URL` 未设时的库连接兜底 |
| `AIBENCH_RETRY_MAX` | `3` | 可选 | HTTP/DB 最大尝试次数（含首次） |
| `AIBENCH_RETRY_BACKOFF` | `1.0` | 可选 | 重试基础退避秒数（指数 + jitter） |
| `AIBENCH_RETRY_BACKOFF_MAX` | `20.0` | 可选 | 退避上限秒 |
| `AIBENCH_CASE_RETRY` | `2` | 可选 | case 因 **infra_error** 整 case 重跑次数 |

**不要把 `.env` 提交到 git。**

---

## 3. 快速上手

### 3.1 离线 smoke（不访问 DB/LLM）

```bash
./scripts/e2e_pipeline.sh --dry-run
```

用 `tests/fixtures` 走抽取→筛选→生成，消融用 fixture 中的 `seed-v0` + mock 矩阵。

### 3.2 推荐：会话 → LLM case → 消融

```bash
set -a && source .env && set +a

# 全流程（会调 DB + 生成时调 LLM）
./scripts/e2e_pipeline.sh \
  --limit 120 \
  --max-cases 8 \
  --matrix configs/runs/ablation-matrix.session.yaml

# 或：仅生成（不重新抽库）+ 消融
./scripts/e2e_pipeline.sh --skip-extract --max-cases 8

# 强制不调 LLM 生成（启发式）
./scripts/e2e_pipeline.sh --heuristic-only --limit 80 --max-cases 8
```

### 3.3 单次跑测

```bash
# 默认：mock agent + auto-v0
./scripts/run_benchmark.sh

# 换真模型
./scripts/run_benchmark.sh \
  --agent configs/agents/openai_compat.yaml \
  --model configs/models/glm52.yaml \
  --case-set auto-v0
```

---

## 4. CLI 子命令与参数

入口：`uv run python -m aibench <cmd>` 或安装后的 `aibench`。

### 4.1 `run` — 单次 Benchmark

| 参数 | 默认 | 作用 |
|------|------|------|
| `--run-config` | `configs/runs/seed-baseline.yaml` | 实验预算、算法名、默认 case set 等 |
| `--agent` | run-config 内 `agent_config` | Agent YAML 路径 |
| `--model` | run-config 内 `model_config` | 模型 YAML 路径 |
| `--case-set` | run-config 内 / `auto-v0` | case 集名称（目录名） |
| `--run-id` | 自动生成 | 本次运行 ID |
| `--output-root` | `runs/` | 结果根目录 |

产物：`runs/<Benchmark>__<时间>_<run_id>/`  
含 `run_manifest.json`、`summary.json`、`results.jsonl`、`tables.json`、`report.md`、`cases/`。

### 4.2 `validate-cases`

| 参数 | 默认 | 作用 |
|------|------|------|
| `--case-set` | `auto-v0` | 校验 JSON Schema 与 case_id 唯一性 |

解析顺序：`benchmarks/ai_coding/cases/<name>`，否则 `tests/fixtures/case_sets/<name>`。

### 4.3 `check-summary`

| 参数 | 默认 | 作用 |
|------|------|------|
| `run_dir`（位置参数） | 必填 | 检查 `summary.json` 是否含结果表核心字段 |

### 4.4 `extract-cases` — 从规范化 JSON

| 参数 | 默认 | 作用 |
|------|------|------|
| `--input` | 必填 | `{sessions:[...]}` 或 session 数组 |
| `--output-dir` | 必填 | 草稿输出目录 |
| `--max-cases` | `50` | 最多写出草稿数 |

### 4.5 `extract-from-db` — 从 `llm_chat_records`

| 参数 | 默认 | 作用 |
|------|------|------|
| `--db-url` | 环境变量 `AIBENCH_DB_URL` | 库连接 |
| `--output-dir` | 必填 | 草稿目录 |
| `--limit` | `300` | 最多扫描行数 |
| `--max-cases` | `30` | 最多输出草稿 |
| `--min-messages` | `3` | `JSON_LENGTH(full_history)` 下限 |
| `--max-messages` | `60` | 上限（过滤超长轨迹） |
| `--all-agents` | 关 | 打开后不限制 User-Agent 含 opencode |
| `--require-gold` | 关 | 只要能从 assistant 抽出代码块的草稿 |
| `--since` / `--until` | 无 | `start_time` 时间窗 `YYYY-MM-DD` |
| `--export-raw` | 无 | 额外写轻量 meta JSON |

### 4.6 `filter-drafts` — 规则筛选

| 参数 | 默认 | 作用 |
|------|------|------|
| `--input-dir` | 必填 | 草稿目录 |
| `--output-dir` | 必填 | keep 输出 |
| `--dropped-dir` | 无 | drop 备份目录 |
| `--report` | 无 | 筛选报告 JSON（kept/dropped/理由） |

丢弃规则示例：运维巡检、HEARTBEAT、任务完成度评测、纯解释/日志 dump 等；keep 需编程信号与足够上下文。

### 4.7 `generate-cases` — 草稿 → 可跑 case

| 参数 | 默认 | 作用 |
|------|------|------|
| `--input-dir` | 必填 | 草稿目录 |
| `--output-dir` | 必填 | 一般写 `benchmarks/ai_coding/cases/auto-v0` |
| `--heuristic-only` | 关 | **不调 LLM**，只规范化草稿 |
| `--max-cases` | `50` | 最多生成条数 |
| `--filter` | 关 | 生成前再跑一遍规则过滤 |

- **默认（无 `--heuristic-only`）**：调 `OPENAI_*` 生成 **stub + pytest + script grader** 的自包含 case；失败最多重试 2 次，再 fallback 启发式。  
- 输出 `metadata.generation` = `llm` 或 `heuristic`；`review_status=needs_review`。

### 4.8 `ablation` — 消融矩阵

| 参数 | 默认 | 作用 |
|------|------|------|
| `--matrix` | 必填 | 矩阵 YAML |
| `--output-root` | `runs/` | 消融输出根目录 |
| `--case-set` | 矩阵内 `case_set` | 覆盖矩阵中的 case 集 |

产物：`runs/ablation_<时间戳>/ablation_summary.json`、`ablation_report.md`（含**项目效果综述表**多行）及各子 run 目录。

---

## 5. 配置文件说明

### 5.1 模型 `configs/models/*.yaml`

| 字段 | 默认（示例） | 作用 |
|------|--------------|------|
| `name` | 配置名 | 人类可读名称 |
| `provider` | `openai_compat` / `mock` | 提供方类型 |
| `model` | 如 `GLM-5.2` | 模型 ID；可被 `OPENAI_MODEL` 覆盖 |
| `base_url` | `null` | 为 null 时用 `OPENAI_BASE_URL` |
| `api_key_env` | `OPENAI_API_KEY` | 从哪个环境变量读 Key |
| `temperature` | `0` | 采样温度 |
| `max_tokens` | `4096` | 单次生成上限 |
| `extra` | `{}` | 预留扩展 |

内置：

- `configs/models/mock-model.yaml` — 无 API  
- `configs/models/glm52.yaml` — 对接 `.env` 中 GLM  
- `configs/models/openai-compat.example.yaml` — 模板  

### 5.2 Agent `configs/agents/*.yaml`

| 字段 | 默认 | 作用 |
|------|------|------|
| `name` | — | Agent 名称（写入结果表） |
| `version` | — | 版本字符串 |
| `adapter` | `mock` / `openai_compat` | 注册表中的实现类 |
| `options.*` | 见下 | 适配器专有选项 |

**mock**

| options | 默认 | 作用 |
|---------|------|------|
| `solve_seed` | `true` | 对已知 fixture seed case 写死正解（仅单测/fixture） |

**openai_compat**

| options | 默认 | 作用 |
|---------|------|------|
| `system_prompt` | JSON 文件重写协议 | 系统提示 |
| `max_tokens` | 可用模型配置覆盖 | 请求 max_tokens |

行为：单轮 Chat，要求模型返回 `{"files":[...],"message":"..."}`，写入 workspace。

### 5.3 单次运行 `configs/runs/*.yaml`

| 字段 | 默认（seed-baseline） | 作用 |
|------|----------------------|------|
| `experiment_name` | `seed-baseline` | 实验名 |
| `algorithm_name` | `Baseline` | 算法名（结果表） |
| `algorithm_version` | `v0.1` | 算法版本 |
| `budget_axis` | `steps` | 预算轴类型 |
| `budget_value` | `max_steps=20` | 预算描述字符串 |
| `branches` | `1` | 分支数 |
| `max_attempts` | `1` | 每 case 尝试次数 |
| `max_steps` | `20` | Agent 最大步数 |
| `max_wall_time_s` | `120` | 每 case 墙钟上限（秒） |
| `selection_strategy` | `first-submit` | 多分支选择策略标签 |
| `case_set` | `auto-v0` | 默认 case 集 |
| `benchmark_name` | `AI-Coding-Assist` | Benchmark 名 |
| `grouping` | `task_type` | 分组口径 |
| `agent_config` | mock yaml | 默认 Agent |
| `model_config` | mock-model yaml | 默认模型 |

### 5.4 消融矩阵 YAML

```yaml
case_set: auto-v0          # 全局默认 case 集
runs:                      # 每一项 = 一次独立实验行
  - experiment_name: mock-baseline
    algorithm_name: Baseline
    agent_config: configs/agents/mock.yaml
    model_config: configs/models/mock-model.yaml
    run_id: sess-mock      # 可选；默认 ablation-<experiment_name>
    case_set: auto-v0      # 可选；覆盖全局
    run_config: ...        # 可选；指定完整 run yaml
```

| 字段 | 默认 | 作用 |
|------|------|------|
| `case_set`（顶层） | 无则 CLI/`seed-v0` 逻辑兜底 | 矩阵默认测评集 |
| `runs[].experiment_name` | `run-N` | 报告中的实验名 |
| `runs[].algorithm_name` | 来自 summary/run-config | 覆盖写入聚合表 |
| `runs[].agent_config` | mock | Agent YAML |
| `runs[].model_config` | mock-model | 模型 YAML |
| `runs[].run_id` | 自动 | 子 run ID |
| `runs[].case_set` | 顶层 case_set | 按行覆盖 |
| `runs[].run_config` | 无 | 指定完整 run 配置 |

示例文件：

- `configs/runs/ablation-matrix.mock.yaml` — 双 mock，适合离线  
- `configs/runs/ablation-matrix.session.yaml` — mock vs GLM，会话候选集  

### 5.5 Case 内 `context.workspace`（现场还原）

| 字段 | 默认 | 作用 |
|------|------|------|
| `mode` | `inline` | `inline` / `snapshot` / `git` / `mixed` |
| `snapshot.path` | 无 | 相对 case-set 的目录或 tar/zip |
| `git.url` / `ref` / `subdir` / `sparse_paths` | 无 | 固定仓库版本 |
| `setup_commands` | `[]` | 落盘后 shell（cwd=workspace） |
| `env` | `{}` | 注入 setup 的环境变量 |
| `strict` | `true` | snapshot/git 失败则 case infra_error |

应用顺序：清空 → snapshot/git → **inline files 覆盖** → setup。

---

## 6. 一键脚本参数

### 6.1 `scripts/run_benchmark.sh`

| 参数 | 默认 | 作用 |
|------|------|------|
| `--agent` | `configs/agents/mock.yaml` | Agent 配置 |
| `--model` | `configs/models/mock-model.yaml` | 模型配置 |
| `--case-set` | `auto-v0` | Case 集 |
| `--run-config` | `configs/runs/seed-baseline.yaml` | Run 配置 |
| `--run-id` | 空（自动） | 运行 ID |

### 6.2 `scripts/e2e_pipeline.sh`

| 参数 | 默认 | 作用 |
|------|------|------|
| `--dry-run` | 关 | 仅 fixture；不访问 DB/LLM 生成；mock 消融 |
| `--skip-extract` | 关 | 跳过抽库，复用 `drafts-from-db` |
| `--heuristic-only` | 关 | 生成阶段不调 LLM |
| `--limit` | `80` | 抽库扫描行数上限 |
| `--max-cases` | `8` | 最多生成 case 数 |
| `--matrix` | `ablation-matrix.mock.yaml` | 消融矩阵；真链路且为 mock 矩阵时会优先切到 `ablation-matrix.session.yaml`（若存在） |
| `--output-root` | `runs/` | 输出根 |

真链路目录约定：

| 路径 | 用途 |
|------|------|
| `benchmarks/ai_coding/cases/drafts-from-db/` | 原始草稿 |
| `.../drafts-kept/` | 筛选后 |
| `.../auto-v0/` | 候选 case 集 |
| `runs/ablation_*/` | 消融汇总与子 run |

---

## 7. 结果怎么读

### 单次 run

| 文件 | 内容 |
|------|------|
| `summary.json` | 聚合指标（成功率、token、墙钟、Agent/模型…） |
| `tables.json` | `overview_row`（综述表一行）+ `general_row`（通用表字段） |
| `report.md` | 人类可读报告 |
| `results.jsonl` | 每 case 一行 |
| `cases/<id>/result.json` | 明细 + workspace 清单 |

主指标：`primary_metric_name = task_success_rate`，评判类型半确定性。

### 消融

`ablation_report.md` 中的 **项目效果综述表** 每行对应矩阵中一个算法/Agent/模型配置。

---

## 8. 常见问题

**Q: auto-v0 为空？**  
先跑 extract → filter → generate，或检查 `.env` 的 DB/API。

**Q: LLM 生成全失败？**  
确认 `OPENAI_BASE_URL`/`KEY`/`MODEL`；GLM 类推理模型可能占满 token，生成器已带短 prompt 重试。仍失败会 fallback 启发式（多为 gold，区分度弱）。

**Q: mock 成功率异常高？**  
可能在用弱 gold 集；LLM 生成的 script 集上 mock 应接近 0%。

**Q: 能否换 mini-swe-agent？**  
实现 `AgentAdapter` 并在 `aibench.agents.registry` 注册，新增 agent YAML 即可。

**Q: 结果表字段很多为空？**  
正常。首版填质量/成本/时间/Agent 核心字段；Fusion 时间拆解、投机指标等未接则为 `null`。

---

## 9. 科学效度审计与并行执行

### 效度审计

```bash
# stub 必须 fail + 污染检测 + 难度/指纹 + 去重
uv run python -m aibench audit-cases --case-set auto-v0 \
  --annotate --report /tmp/audit.json --fail-on-error

# 发布时强制审计通过
uv run python -m aibench promote --from-set auto-v0 --to-set prod-v0 \
  --require-audit --case-id my_case
```

| 检查 | 含义 |
|------|------|
| stub_fail | script grader 在初始 workspace 上必须失败 |
| contamination | gold/key_lines 不得已在初始上下文中 |
| difficulty | 启发式 easy/medium/hard |
| fingerprint | prompt+paths 去重信号 |
| content_fingerprint | 整个 case set 内容指纹（写入 run manifest） |

报告中含 **Wilson 95% CI** 与按 `task_type` / `difficulty` 分层成功率。

### 并行

| 位置 | 参数 | 默认 | 说明 |
|------|------|------|------|
| `aibench run` | `--workers N` | 1（或 run-config `case_workers`） | **case 级**并行 |
| run yaml | `case_workers` | 1 | 同上 |
| `generate-cases` | `--workers N` | 1 | 生成并行 |
| `ablation` | `--parallel N` | 1 | **实验行**并行 |

每个 case 使用独立 Agent 实例与独立 workspace，mock 上 workers=1 与 workers=2 成功率一致。

生成后一键审计：

```bash
uv run python -m aibench generate-cases ... --audit --secrets-scan --workers 4
```

---

## 10. 扩展命令（遗留能力补全后）

### 发布候选 → 正式集

```bash
# 预演
uv run python -m aibench promote --from-set auto-v0 --to-set prod-v0 --dry-run

# 指定 case 发布（推荐）
uv run python -m aibench promote --from-set auto-v0 --to-set prod-v0 \
  --case-id gitcode_api_v4_to_v5 --case-id py_stack_lifo_bug
```

门控：schema 合法、默认要求 `grader.mode=script`、拒绝 `weak_grader`、secrets 扫描干净。

### 脱敏扫描 / Snapshot

```bash
uv run python -m aibench secrets-scan --case-set auto-v0 --report /tmp/secrets.json
uv run python -m aibench snapshot-skeleton --case-set auto-v0
```

### 消融增强

```bash
uv run python -m aibench ablation \
  --matrix configs/runs/ablation-matrix.session.yaml \
  --parallel 2 \
  --baseline-experiment mock-baseline \
  --export-csv --export-xlsx
# 默认剔除 weak_grader；需要保留时加 --allow-weak-grader
```

### LLM 软过滤

```bash
uv run python -m aibench filter-drafts \
  --input-dir benchmarks/ai_coding/cases/drafts-from-db \
  --output-dir benchmarks/ai_coding/cases/drafts-kept \
  --llm-soft
```

### 新 Agent

| adapter | 配置 | 说明 |
|---------|------|------|
| `tool_loop` | `configs/agents/tool_loop.yaml` | 多步 list/read/write/bash/submit |
| `shell` | `configs/agents/shell.example.yaml` | 外部 CLI（可包 mini-swe 等） |

费用：设 `AIBENCH_USD_PER_MTOK` 或 input/output 单价（每百万 token USD）。

---

## 11. 命令速查

```bash
# 环境
uv sync && set -a && source .env && set +a

# 抽库
uv run python -m aibench extract-from-db \
  --output-dir benchmarks/ai_coding/cases/drafts-from-db \
  --limit 120 --max-cases 30 --require-gold

# 筛选
uv run python -m aibench filter-drafts \
  --input-dir benchmarks/ai_coding/cases/drafts-from-db \
  --output-dir benchmarks/ai_coding/cases/drafts-kept \
  --report /tmp/filter_report.json

# LLM 生成候选集
uv run python -m aibench generate-cases \
  --input-dir benchmarks/ai_coding/cases/drafts-kept \
  --output-dir benchmarks/ai_coding/cases/auto-v0 \
  --max-cases 8

uv run python -m aibench validate-cases --case-set auto-v0

# 消融
uv run python -m aibench ablation \
  --matrix configs/runs/ablation-matrix.session.yaml \
  --case-set auto-v0

# 单次
uv run python -m aibench run \
  --agent configs/agents/openai_compat.yaml \
  --model configs/models/glm52.yaml \
  --case-set auto-v0
```
