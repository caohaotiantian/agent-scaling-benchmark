# AI-Coding-Assist Benchmark（Agentic Scaling）

从真实 AI 编程会话构建可机评用例，在固定 case 集上替换 **Agent / 模型** 跑测，并产出对齐项目 **结果表** 口径的实验报告。

---

## 文档入口（HTML 统一展示）

**推荐打开文档站：** [`docs/html/index.html`](docs/html/index.html)

| 页面 | 说明 |
|------|------|
| **[交接文档](docs/html/handoff.html)** | **接手工作请先读这一页**：成果、实测数据、经验教训、后续计划 |
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

产物：`benchmarks/ai_coding/cases/auto-v0/`。每条 case 带 `metadata.tier`（T1–T5），
层级由源 trace 的过程信号推导、再由结构不变量核验。

### 校准与选题（让区分度可验证）

结构不变量保证用例「该有区分度」，跑一遍才知道有没有：

```bash
# 用锚点面板（弱/中/强）实测每条 case 的通过率与区分指数
uv run python -m aibench calibrate-cases --case-set auto-v0 --repeats 3

# 按区分度挑出可用题，组成新集合
uv run python -m aibench select-cases \
  --calibration runs/calibration_<ts>/calibration.json \
  --from-set auto-v0 --to-set disc-v0
```

淘汰：`p_hat > 0.9`（送分题）、`p_hat < 0.05`（多半是坏题）、点二列相关 `< 0.15`（噪声题）。
锚点面板见 `configs/runs/anchor-panel.yaml`，**必须同时变化模型与 agent 两条轴**。

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
MySQL 生产 trace ──► 草稿（含过程信号 → 建议层级 T1–T5）
                       ↓
                    规则/LLM 筛选
                       ↓
     分层生成 ──► 消毒定级（去缺陷标记 / 去题面泄露 / 拆隐藏测试）
                       ↓
     效度门禁：stub 必 fail（下界）+ 参考解必 pass（上界）+ 分层不变量
                       ↓
     经验校准（锚点面板 × 重复）──► 按区分度选题 ──► 可用 case 集
                       ↓
              Agent/模型配置（可替换）→ 判分
                       ↓
   summary / tables / ablation_report（分层成功率 + McNemar 配对检验）
```

| 要点 | 说明 |
|------|------|
| **分层** | T1 直接修复 / T2 定位修复 / T3 隐藏规格 / T4 跨文件检索 / T5 迭代自修复 |
| **区分度** | 隐藏测试 + 保护路径消灭捷径；校准淘汰送分题与噪声题 |
| **消融** | 同一 case 集上对比多组 Agent/模型，每行只变一条轴 |
| **四条轴** | 基线 / 模型（glm51 vs glm52）/ Agent（单轮 vs tool_loop）/ 采样（k=1 vs k=5） |
| **采样扩展** | `pass@k − pass@1` 是重复采样暴露的上限，`成功率 − pass@1` 是策略实际吃到的部分 |
| **成本轴** | `success_rate @ token budget` 曲线 + 相对基线 token 倍数 —— 5 倍 token 换来的提升不等于等成本提升 |
| **显著性** | 同一 case 集上用 McNemar 配对检验，比各自的 Wilson CI 灵敏 |
| **样本量** | `plan-sample-size` 反推所需题量（按实测不一致率，不是拍脑袋） |
| **多语言** | Python(pytest) 与 JavaScript(`node --test`)，只注册本机能真跑的语言 |
| **生产配置** | 见 `configs/`（无 mock；mock 仅在 `tests/fixtures`） |
| **主指标** | `task_success_rate`（半确定性），口径不变；pass@k 等并列新增 |

### 采样扩展与成本（pass@k）

```bash
uv run python -m aibench run --run-config configs/runs/passk.yaml --case-set disc-v0
```

`temperature: 0` 下 k 次采样是同一个样本，pass@k 恒等于 pass@1 —— runner 会告警并写进 manifest。
采样实验请用 `configs/models/glm52-sampling.yaml`。

### 要多少题才能得出结论

```bash
uv run python -m aibench plan-sample-size --delta 10 --from-ablation runs/ablation_<ts>/ablation_summary.json
```

配对检验只从「两个配置结论不同」的 case 学到东西，所以不一致率和效应量同样决定题量。

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
