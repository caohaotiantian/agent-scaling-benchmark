# AI-Coding-Assist Benchmark（Agentic Scaling）

从真实 AI 编程会话构建可机评用例，在固定 case 集上替换 **Agent / 模型** 跑测，并产出对齐项目 **结果表** 口径的实验报告。

---

## 文档入口（HTML 统一展示）

**推荐打开文档站：** [`docs/html/index.html`](docs/html/index.html)

| 页面 | 说明 |
|------|------|
| **[文档站首页](docs/html/index.html)** | 全部 HTML 页面索引与导航 |
| **[项目介绍演示](docs/html/project-overview.html)** | 目标、架构、命令、科学效度、产物、设计表关系 |
| **[参考手册](docs/html/reference.html)** | 参数级权威参考（CLI / 配置 / Schema / 映射） |
| [用户向导](docs/html/user-guide.html) | 操作步骤与速查 |
| [结果表设计报告](docs/html/agentic-scaling-benchmark.html) | 协议与表结构源头 |
| [结果表字段字典](docs/html/tables.html) | 综述表 / 通用总表列定义 |
| [生产配置](docs/html/configs.html) | `configs/` 生产配置（无 mock） |
| [未尽事项](docs/html/remaining-work.html) | 已知缺口与后续 |

Markdown 源保留于 `docs/*.md`、`configs/README.md`、`docs/html/_src/`（供编辑与 diff）；**展示以 `docs/html/*.html` 为准**。字段字典源为 `docs/html/_src/tables.md`（构建时会把伪表头行渲染成标题/说明）。  
重建 HTML：

```bash
uv run python scripts/build_docs_html.py
```

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

| 要点 | 说明 |
|------|------|
| **消融** | 同一 case 集上对比多组 Agent/模型，不是删网络层 |
| **生产配置** | 见 `configs/`（无 mock；mock 仅在 `tests/fixtures`） |
| **与设计表** | `tables.json` / `ablation_report.md` 填设计报告与字段字典定义的列 |
| **主指标** | `task_success_rate`（半确定性） |

`--limit` / `--max-cases` 有默认值，**不传也不会全库无限扫**。详见 [参考手册](docs/html/reference.html)。

---

## 目录结构（摘要）

```text
docs/html/                      统一 HTML 文档站（展示入口）
docs/*.md                       Markdown 源（可编辑）
configs/                        生产 Agent / 模型 / Run / 消融矩阵
src/aibench/                    可执行 harness
benchmarks/ai_coding/           case schema 与 case 集
scripts/build_docs_html.py      重建 HTML 文档站
runs/                           实验结果（本地生成，gitignore）
```

---

## 开发

```bash
./scripts/lint.sh
uv run pytest tests/ -q
uv run python scripts/build_docs_html.py   # 文档 HTML
```

Python ≥ 3.11，推荐使用 `uv`。
