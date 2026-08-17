# AI-Coding-Assist Benchmark — 用户使用手册

> **⚠️ 本文内容早于 2026-08-10 的主线转向**，描述的是已被否决的「正向生成」路线，且未提及 `bare_model` 适配器。当前状态以 [`docs/HANDOFF.md`](HANDOFF.md) **§0.-1**（最新的一块；§0 记的是 2026-08-10 的状态，且指向一个已删除的用例集）为准，操作步骤以 [用户手册 manual.html](html/manual.html) 为准。
> 文档站首页：[docs/html/index.html](html/index.html)

面向：从真实会话构建候选测评集、一键跑测、Agent/模型消融，并产出对齐 Agentic Scaling 结果表的报告。

**参数级说明**见 [`docs/REFERENCE.md`](REFERENCE.md)；设计论证与门禁规则见 [参考资料 HTML](html/reference.html)。两者是不同的文档，不是同一份的两种形态。  
**浏览器演示总览**见 [`docs/html/overview.html`](html/overview.html)。

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
| **published / prod-v0** | 人工 `promote` 后的正式集（流水线不自动写入） |

生产默认：**无 mock**。Agent = `openai_compat`，模型 = `GLM-5.2`，矩阵对比 `openai_compat` vs `tool_loop`。

---

## 2. 环境准备

```bash
cd agent-scaling-benchmark
uv sync --extra dev --extra grading
cp .env.example .env   # 按需填写
./scripts/install-hooks.sh   # 提交前 ruff format + import + lint
```

CLI 启动时会加载项目根 `.env`。也可：

```bash
set -a && source .env && set +a
```

### 2.1 最低环境变量

| 场景 | 需要 |
|------|------|
| 从 MySQL 抽会话 | `AIBENCH_DB_URL` |
| LLM 生成 case / 真 Agent 跑测 | `OPENAI_API_KEY` + `OPENAI_BASE_URL`（建议再设 `OPENAI_MODEL`） |
| 离线 dry-run | 可不填 |

完整变量表见 [`REFERENCE.md` §6 环境变量完整参考](REFERENCE.md#6-环境变量完整参考)。

**不要把 `.env` 提交到 git。**

### 2.2 代码风格

| 命令 | 作用 |
|------|------|
| `./scripts/lint.sh` | format + import 修复 + lint |
| `uv run pytest tests/ -q` | 单测 |

---

## 3. 快速上手

### 3.1 离线 smoke（不访问 DB/LLM）

```bash
./scripts/e2e_pipeline.sh --dry-run
```

### 3.2 只要生成测试用例（推荐先做这一步）

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
```

产物：`benchmarks/ai_coding/cases/auto-v0/*.json`。  
**不要**用完整 `e2e_pipeline.sh` 若你还不想消融（脚本末尾会跑 ablation）。

### 3.3 完整生产流水线（含消融）

```bash
./scripts/e2e_pipeline.sh --limit 100 --max-cases 8
```

### 3.4 已有 auto-v0 后单次跑测

```bash
./scripts/run_benchmark.sh
# 默认：openai_compat + glm52 + auto-v0 + baseline.yaml
```

### 3.5 消融对比

```bash
uv run python -m aibench ablation \
  --matrix configs/runs/ablation-matrix.yaml \
  --case-set auto-v0 \
  --baseline-experiment openai-compat-glm52 \
  --export-csv
```

---

## 4. `--limit` 与 `--max-cases`（务必理解）

| 参数 | CLI 默认 | e2e 默认 | 含义 |
|------|----------|----------|------|
| `--limit` | 抽库 300 | 100 | DB 最多扫描多少行 |
| `--max-cases`（抽库） | 30 | max-cases×3 | 最多写多少草稿 |
| `--max-cases`（生成） | 50 | 8 | 最多写多少最终 case |

**不传参 = 用默认，不是无限扫库。**

---

## 5. 配置怎么选

| 需求 | 配置 |
|------|------|
| 默认单次生产跑测 | `configs/runs/baseline.yaml` |
| 多步工具 Agent | `configs/agents/tool_loop.yaml` + `runs/baseline-tool-loop.yaml` |
| 对照消融 | `configs/runs/ablation-matrix.yaml` |
| 换模型 | `configs/models/glm51.yaml` 或 `qwen37.yaml` |
| 包外部 CLI | `configs/agents/shell.yaml`（填 `command_template`） |
| 单测 / dry-run mock | **仅** `tests/fixtures/configs/` |

说明见 [用户手册 manual.html](html/manual.html) 与 [`REFERENCE.md` §7 生产配置体系](REFERENCE.md#7-生产配置体系-configs)。

---

## 6. CLI 与脚本（操作向）

入口：`uv run python -m aibench <cmd>`。

| 命令 | 你什么时候用 |
|------|----------------|
| `extract-from-db` | 从 MySQL 建草稿 |
| `filter-drafts` | 去掉运维/闲聊/非编程会话 |
| `generate-cases` | 草稿变成可 pytest 的 case |
| `validate-cases` | Schema 校验 |
| `audit-cases` | stub 必须 fail、污染检测 |
| `run` | 一组 Agent/模型跑一遍 |
| `ablation` | 多组配置横向对比 |
| `promote` | 人工发布到 prod-v0 |
| `secrets-scan` | 扫密钥 |

| 脚本 | 作用 |
|------|------|
| `scripts/run_benchmark.sh` | 生产单次跑测 |
| `scripts/e2e_pipeline.sh` | DB→case→消融；`--dry-run` 仅 fixture |

**完整参数清单（每个 flag 的默认值与作用）**：  
[REFERENCE HTML](html/reference.html) · 演示页 [§七](html/overview.html#modules)。

---

## 7. 结果怎么读

### 单次 run

`runs/AI-Coding-Assist__<时间>_<run_id>/`

| 文件 | 用途 |
|------|------|
| `summary.json` | 成功率、Wilson CI、Token、成本估算 |
| `tables.json` | 综述表一行 + 通用表关键字段 |
| `report.md` | 人类可读 |
| `results.jsonl` | 每 case 明细 |

主指标：`task_success_rate`（半确定性）。

### 消融

`runs/ablation_<时间>/ablation_report.md` — **多行对比表，汇报首选**。

### 用途速查

| 目的 | 打开 |
|------|------|
| 汇报选型 | `ablation_report.md` / `overview_row` |
| 查失败原因 | `report.md` + `cases/<id>/result.json` |
| 复现 | `run_manifest.json` 中的 fingerprint 与配置路径 |

字段与设计表的对应关系见 [`REFERENCE.md` §16 运行产物与结果表映射](REFERENCE.md#16-运行产物与结果表映射)（至 §18）。

---

## 8. 科学效度与并行（实用）

**科学效度** = 可自动执行的 case 质量门禁（实现于 `validity.py`），保证指标差异尽量来自 Agent/模型，而非坏题/答案泄漏。  
完整门禁列表与算法逻辑见 [`REFERENCE.md` §14 科学效度](REFERENCE.md#14-科学效度scientific-validity定义门禁与逻辑)；设计论证见 [参考资料 HTML](html/reference.html) 的「效度门禁」一节，演示页见 [§八](html/overview.html#ablation)。

| 级别 | 门禁示例 | 是否阻断 `validity_ok` |
|------|----------|------------------------|
| **error** | stub 初始必须 fail；gold/key_line 不得已在 context；prompt 过短 | 是 |
| **warn** | prompt 大代码块、weak_grader 标记、重复指纹 | 否 |
| **注解** | difficulty、fingerprint、集级 content_fingerprint | — |

```bash
# 生成后审计
uv run python -m aibench audit-cases --case-set auto-v0 \
  --annotate --fail-on-error

# 发布（可叠加 --require-audit）
uv run python -m aibench promote --from-set auto-v0 --to-set prod-v0 \
  --require-audit --case-id <id>

# 并行
uv run python -m aibench run --workers 4
uv run python -m aibench generate-cases ... --workers 4
uv run python -m aibench ablation --matrix ... --parallel 2
```

报告侧：Wilson 95% CI、有效 case（排除 infra_error）、按 task_type/difficulty 分层。

---

## 9. 常见问题

**Q: auto-v0 为空？**  
先跑 extract → filter → generate；检查 `.env` 的 DB/API。

**Q: 只想生成 case？**  
不要跑完整 `e2e_pipeline.sh`（末尾有消融）。按 §3.2。

**Q: LLM 生成全失败？**  
确认 `OPENAI_*`；失败会 fallback 启发式（多为 gold，区分度弱）。

**Q: 结果表很多空列？**  
正常。Fusion 时间拆解等本 harness 无数据源，保持 `null`。

**Q: 能否接 mini-swe-agent？**  
用 `shell` adapter 配 `command_template`，或实现新 adapter 并注册。

---

## 10. 文档索引

四行标签修正于 2026-08-17：`reference.html` 不是参数级参考手册（那是
`docs/REFERENCE.md`），`manual.html` 不讲生产配置（那是 `configs/README.md`），
`reference.html` 也不是结果表设计报告或字段字典 —— 后者没有替代页，随旧站一起删除了。

| 文档 | 说明 |
|------|------|
| [docs/html/index.html](html/index.html) | 文档站首页（四页 HTML） |
| [参考手册 docs/REFERENCE.md](REFERENCE.md) | **参数级权威参考**：CLI 全参数、配置字段、Schema、产物映射 |
| [参考资料 reference.html](html/reference.html) | 设计论证、数据格式、门禁规则、已发布校准数据清单 |
| [项目介绍 overview.html](html/overview.html) | 背景、流水线架构、反向构造原理、实测分布 |
| [用户手册 manual.html](html/manual.html) | 本向导的 HTML 展示 |
| [生产配置 configs/README.md](../configs/README.md) | Agent / 模型 / Run / 消融矩阵的配置说明 |
| [已发布校准数据](../benchmarks/ai_coding/calibrations/README.md) | 13 份校准文件的口径与复算方法 |
| [未尽事项 REMAINING_WORK.md](REMAINING_WORK.md) | 已知缺口（内容截至 2026-08-04） |
| [审计 AUDIT-2026-08-17.md](AUDIT-2026-08-17.md) | 可复现性缺口与已发布数字的核验 |

> 字段字典（旧 `tables.html`）**没有替代页**。它随旧站一起删除，内容未迁移。

---

## 11. 命令速查

```bash
uv sync --extra dev --extra grading && set -a && source .env && set +a

# 生成用例
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

# 跑测 / 消融
./scripts/run_benchmark.sh
uv run python -m aibench ablation --matrix configs/runs/ablation-matrix.yaml --export-csv

# 全流程
./scripts/e2e_pipeline.sh --limit 100 --max-cases 8
```
