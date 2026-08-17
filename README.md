# AI-Coding-Assist Benchmark（Agentic Scaling）

从真实 AI 编程会话构建可机评用例，在固定 case 集上替换 **Agent / 模型** 跑测，并产出对齐项目 **结果表** 口径的实验报告。

---

> ## 接手须知
>
> **先读 [`docs/SESSION-2026-08-14.md`](docs/SESSION-2026-08-14.md)**（最近一次会话），
> 再读 [`docs/HANDOFF.md`](docs/HANDOFF.md) 的 **§0.-1**（最新的一块；§0 是最旧的三块之一，
> 且指向一个已删除的用例集）。那里有三条限定条件，不看会得出错误结论：
>
> - **当前所有难度数字都需要重测。** 适配器有三处缺陷、系统性惩罚推理模型；同一模型在修复前后的四轮消融里通过率跨度 58pp（25.8% → 83.9%）。注意这个跨度**同时含协议变更**（前三轮是 `openai_compat`，第四轮才是 `tool_loop`）——`runs/` 下没有修复前的 `tool_loop` 测量，无法把它单独归给某一个锚点。
> - **锚点面板强弱反转**（weak 85.2% > strong 77.4%），重新指派之前不能做校准。
> - **`_revmixed` 31 条里 12 条是抄写题**，`test_reads_source_text` 门禁上线后剩 19 条有效。
>
> 一句话状态：**harness 本身可用且经过硬化，但目前没有一个可用于下结论的用例集。**

## 文档入口

| 页面 | 说明 |
|------|------|
| **[会话交接 `docs/SESSION-2026-08-14.md`](docs/SESSION-2026-08-14.md)** | **接手请先读这一份**：最新状态与已知缺陷。§5.4 记录了一个会删掉 worktree 的测试（2026-08-17 定位到 `materialize_workspace` 会先删除目标目录，并加了守卫 `tests/conftest.py::guard_the_checkout` —— 守卫抓的是复发，不是病因），§5.3 记录 PII 与消毒缺口 |
| [会话交接 `docs/SESSION-2026-08-11.md`](docs/SESSION-2026-08-11.md) | 上一次会话：未决问题（不含推荐方案）、已知缺陷 |
| [审计回应 `docs/AUDIT-RESPONSE-2026-08-17.md`](docs/AUDIT-RESPONSE-2026-08-17.md) | 对 `docs/AUDIT-2026-08-17.md` 的逐条处置：改了什么、什么留给 owner、以及对已发布提交信息中六处数字的更正 |
| [项目交接 `docs/HANDOFF.md`](docs/HANDOFF.md) | 更早的状态、限定条件与经验教训。**从 §0.-1 读起** |
| [审计 `docs/AUDIT-2026-08-17.md`](docs/AUDIT-2026-08-17.md) | 第三方复算审计：可复现性缺口、已发布数字的核验、修复排序 |
| [用户手册 `docs/USER_GUIDE.md`](docs/USER_GUIDE.md) | 端到端操作向导。**内容截至 2026-08-04，早于主线转向反向构造** |
| [文档站首页](docs/html/index.html) | 四页 HTML 站的导航 |
| [项目介绍](docs/html/overview.html) | 背景、流水线架构、反向构造原理、实测分布 |
| [用户手册](docs/html/manual.html) | 环境准备、端到端流程、参数含义、故障排查 |
| [参考资料](docs/html/reference.html) | 设计依据、数据格式、门禁规则、已发布校准数据 |
| [参考手册 `docs/REFERENCE.md`](docs/REFERENCE.md) | **CLI 与配置的参数级权威参考**（HTML 站不含这部分） |
| [未尽事项](docs/REMAINING_WORK.md) | 已知缺口（注意：内容截至 2026-08-04，早于主线转向） |

> **`docs/REFERENCE.md` 与 `docs/html/reference.html` 是两份不同的文档，不是同一份的两种形态。**
> 前者约 1,970 行，覆盖 CLI 全参数、配置字段、Schema、产物映射、FAQ；
> 后者 300 行，覆盖设计论证、数据格式、门禁规则、已发布校准数据清单。
> 查参数去 `.md`，查「为什么这么设计」去 `.html`。
> 文档站只从 `docs/html/_src/*.html` 三个手写片段构建，**改 `docs/*.md` 不会改变站点内容**。

重建 HTML：

```bash
uv run python scripts/build_docs_html.py    # 只重建 index / overview / manual / reference 四页
uv run python scripts/check_doc_links.py    # 断链检查
```

---

## 快速开始

```bash
uv sync --extra dev --extra grading
./scripts/install-hooks.sh    # ruff format + import + lint + secrets scan

cp .env.example .env          # 填写 AIBENCH_DB_URL / OPENAI_*
set -a && source .env && set +a

uv run python -m aibench doctor   # 先确认这台机器能产出可比的测量
```

### 先跑通：clone 里唯一离线可用的集合

案例集是 gitignore 的（见 §「为什么仓库里没有 case」），**唯一随仓库分发的是 `seed-v0`**
——四条 committed 的 fixture 用例，不需要数据库、不需要网关：

```bash
uv run python -m aibench validate-cases --case-set seed-v0   # -> OK case_set=seed-v0
uv run python -m aibench run --run-config tests/fixtures/configs/runs/baseline.mock.yaml \
  --case-set seed-v0        # mock 适配器，不花钱 -> success_rate=1.000 (4/4)
```

`uv run python -m aibench audit-cases --case-set seed-v0` 会报 **0/4 通过**，这是对的、
不是仓库坏了：这四条是最小 fixture，三条没有随附参考解（`no_reference_solution`），
一条的 key line 已经出现在上下文里（`contamination_keyline_in_context`）。
它们存在是为了让单元测试有东西可跑，不是为了做合格用例的样板。

下面所有 `--case-set auto-v0` 的命令都要先自己建集，那一步需要数据库与网关。

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

**当前主线是「反向构造」：缺陷不由模型发明，而是回放 trace 里工程师真的改过的那次编辑。**

```text
MySQL 生产 trace ──► 草稿 + 回放 edit 得到 (pre, post) 真实前后版本
                       ↓
                    规则筛选 + 抽取端谓词
                       （import 可满足 / 非测试文件 / 编辑不只是注释）
                       ↓
     反向构造：stub = pre，参考解 = post，LLM 只写测试与现象态题面
                       ↓
     效度门禁：stub 必 fail（下界）+ 参考解必 pass（上界）
               + 测试不得 grep 源码文本（否则两道门禁按构造都会过）
                       ↓
     经验校准（锚点面板 × 重复）──► 按区分度选题 ──► 可用 case 集
                       ↓
              Agent/模型配置（可替换）→ 判分
                       ↓
   summary / tables / ablation_report（成功率 + McNemar 配对检验）
```

> **正向生成（让模型编题）已被三次干预实验判定失效**，代码仍在（`generate-cases` 不带
> `--reverse`），但不再是主线：输入怎么变，输出难度不动 —— case 体积相关 +0.07，
> 源 trace 复杂度 |r| < 0.225 且符号相反，抑制题面泄露完全无效。详见 `docs/HANDOFF.md`。
>
> **T1–T5 分层机制存在于 `tiers.py` 且有完整不变量检查，但当前主线产出的用例 `tier` 全为空** ——
> 反向构造路径绕过了 `settle_tier`。分层成功率报表因此只有一列 `unknown`。

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

`--limit` / `--max-cases` 有默认值，**不传也不会全库无限扫**。详见 [参考手册 `docs/REFERENCE.md`](docs/REFERENCE.md) §8.5 ——参数级细节在 `.md`，不在 HTML 站，这正是上面那条规则说的。

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
uv run ruff format --check src tests scripts && uv run ruff check src tests scripts
uv run pytest tests/ -q                    # 用例数以这条命令的输出为准，不在文档里写死
uv run python -m aibench doctor            # python / node / opencode / 评分环境 / 沙箱自检
uv run python scripts/build_docs_html.py   # 文档 HTML（只从 docs/html/_src/*.html 构建）
uv run python scripts/check_doc_links.py   # 断链检查
```

`./scripts/lint.sh` 会**改写**源码（`ruff check --fix` + `ruff format`），所以它是修复工具，
不是门禁；门禁用上面第一行。

Python **3.13**（见 `.python-version`；`uv.lock` 在不同解释器上会解析出不同的 numpy），
Node **≥ 22.18**（见 `.nvmrc`；22.18 以下 `node --test` 发现不了 TypeScript 测试却退出 0，
也就是**判为通过**）。

---

## English

This repository builds machine-gradable coding-benchmark cases from real AI-assisted programming
sessions, then swaps the agent and the model on a fixed case set to compare them. Everything a
reader needs is in Chinese below; these are the three qualifications that decide whether a number
here means anything, and they are the reason this section exists at all:

1. **Every difficulty figure needs re-measuring.** Three adapter defects systematically penalised
   reasoning models. The same model moved 58 points (25.8% → 83.9%) across four ablation rounds
   with no model change — and that span also contains a protocol change, so it cannot be
   attributed to any single fix.
2. **The anchor panel inverted** (weak 85.2% > strong 77.4%). No calibration is meaningful until
   the anchors are reassigned.
3. **12 of `_revmixed`'s 31 cases graded transcription rather than behaviour**; 19 survive the
   `test_reads_source_text` gate.

In one line: **the harness works and has been hardened, but there is currently no case set fit to
draw a conclusion from.**

Licence: see `LICENSE` — this is not open source. Case sets, drafts and run directories are
excluded from the repository on purpose and are **not** covered by any grant; ask before
requesting them. Access is granted by the owner named in `CODEOWNERS`.
