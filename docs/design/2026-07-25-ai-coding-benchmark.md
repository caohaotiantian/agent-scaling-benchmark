# AI 辅助编程 Benchmark 设计

| 字段 | 值 |
|------|-----|
| 状态 | implemented (harness v0.1) |
| 日期 | 2026-07-25 |
| 任务 | Phase A 设计 + Phase B harness 首版 |
| 关联文档 | `agentic-scaling-benchmark.html`、`agentic_scaling_benchmark_tables.md` |

---

## 1. Background and Purpose

### 1.1 背景

Agentic Scaling 项目需要在统一 Benchmark 协议下横向比较不同算法（单模型基线、投机执行、多模型组合、Best-of-N、Verifier 等）的质量、成本与耗时收益。已有结果表设计覆盖：

- **确定性任务**：SWE-Bench、算子生成、漏洞挖掘（`resolved_rate` / `pass_rate`）
- **偏好型任务**：AI 辅助编程、IDE Agent、Arena-style（`win_rate` / `Elo` / `mean_rating`）

本任务补齐 **「AI 辅助编程」** 这一领域 Benchmark 的协议、测评集构建与可运行 harness。

### 1.2 为什么要做

若只评测 SWE-Bench 类「单 issue 修到测试绿」，无法代表真实 AI 编程助手场景：多轮澄清、局部编辑、在不完整上下文中完成子任务、用户可采纳但无唯一金标。缺少该 Benchmark 会导致 scaling 算法优化目标偏斜。

### 1.3 若不做的后果

- 算法在确定性修复上好看，在真实助手交互上无证据
- 结果表「AI Coding Arena」行长期停留在占位样例
- 真实会话数据无法沉淀为可复现、可替换 Agent/模型的实验资产

---

## 2. Deliverables

### Phase A（设计）

- [x] 本设计文档：任务定义、case 协议、判分、指标映射、会话→case 流水线
- [x] 与通用结果总表 / 综述表的字段映射表

### Phase B（可运行 harness，首版）

- [x] Case schema（JSON Schema + 样例 case set）
- [x] 可插拔 Agent 适配器接口 + 参考实现（mock + OpenAI-compatible）
- [x] 可配置模型（base_url / model / temperature 等）
- [x] 一键脚本：`scripts/run_benchmark.sh` / `python -m aibench run`
- [x] 判分器：确定性脚本 + gold；LLM-judge 为接口预留 stub
- [x] 落盘：`runs/<run_id>/` 下 `run_manifest.json`、`summary.json`、`results.jsonl` 及 case 明细
- [x] 结果导出：综述表行 + 通用结果总表行（Markdown/JSON，字段对齐 `agentic_scaling_benchmark_tables.md`）
- [x] 会话抽取接口：规范化 JSON export → case 草稿（DB 连接待用户提供后接 SQL 适配）
- [x] README 与最小测试

---

## 3. Scope Boundary

### 3.1 在范围内

| 项 | 说明 |
|----|------|
| Benchmark 名称 | `AI-Coding-Assist`（内部名；对外可称「AI 辅助编程」） |
| 任务形态 | 从真实/半真实会话抽象的 **单 case 编程任务**：给定上下文 + 用户目标，Agent 产出可验收产物 |
| 主评测范式 | **半确定性**：可执行验收优先；不可执行部分用 LLM-judge / 金标对比；可选 pairwise 扩展 |
| 指标 | 主指标 `task_success_rate`；辅指标耗时、Token、步数、工具调用；预留 `mean_rating` / `win_rate` |
| Agent 替换 | 统一 `AgentAdapter` 协议；配置切换 agent 与 model |
| 结果格式 | 对齐项目效果综述表 + 通用结果总表（可空字段填 `null` / 空） |

### 3.2 明确不在范围内（首版）

| 项 | 原因 |
|----|------|
| 完整 IDE 内嵌仿真（VS Code/JetBrains UI） | 工程量大；首版用 workspace 快照 + 终端 Agent 足够 |
| 真人 Arena 评审平台 | 流程与权限独立；协议预留 `pairwise` case 类型即可 |
| Fusion/投机算法本体实现 | 本仓库是 **Benchmark 与 runner**；算法作为可替换 Agent/后端接入 |
| SWE-Bench 官方 harness 复用 | 任务协议不同；仅结果表字段对齐 |
| 大规模自动从 DB 生成并免审上线全量 case | 质量风险高；首版 seed + 抽取流水线 + 人工审核门槛 |
| 生产级成本计费与 GPU 监控 | 字段预留，首版可填 `null` |
| 多语言全覆盖 | 首版以 Python 为主，schema 不绑死语言 |

### 3.3 质量预算（声明）

| 属性 | 决策 |
|------|------|
| dry-run 端到端 | mock agent 在 seed case（≥3）上必须 0 非基础设施错误退出，并产出合法 `summary.json` |
| 结果表字段 | 通用表必填标识/口径/主质量/成本时间（有则填，无则 null）；禁止自造冲突列名 |
| 延迟/吞吐 SLO | **首版不设**（本地与 API 环境差异大）；排除在质量预算外 |
| UI/无障碍 | 不适用（CLI） |

---

## 4. Key Design Decisions

### 决策 1：主评测范式 — 半确定性优先（非纯 Arena）

| 问题 | AI 辅助编程常无唯一正确答案，但 Agentic Scaling 需要可重复、可横向对比的主指标。 |
|------|------|
| 选项 A | **半确定性**：脚本/测试/结构检查定义成功；辅以 LLM-judge 分数 | **选中** |
| 选项 B | 纯 Arena pairwise + Elo | 贴近 HTML 示例，但依赖稳定 judge、battle 矩阵，首版难一键复现 |
| 选项 C | 仅人工采纳率 | 无法自动化 scaling 实验 |
| 理由 | 与综述表「成功率」兼容；真实会话里大量任务可定义弱金标（用户最终采纳的代码、最终 diff、是否继续追问失败等）。Arena 作为 case 子类型 `pairwise` 预留，不阻塞主路径。 |
| 拒绝 B/C | B 实现与方差控制成本高；C 无法支撑算法迭代闭环 |

**主指标**：`task_success_rate` = 成功 case 数 / 有效 case 数  
**成功定义**：grader 返回 `passed=true`（脚本全部通过，或 LLM-judge score ≥ `judge_threshold`，或与 gold 的约定匹配规则通过）。

### 决策 2：Case 来源 — 真实会话抽取 + seed 启动

| 问题 | 如何得到可复现 case set。 |
|------|------|
| 选项 A | **DB 会话筛选 → case 草稿 → 人工/半自动审核入库** + 仓库内 seed set | **选中** |
| 选项 B | 仅合成/手写 case | 无真实分布，外部效度差 |
| 选项 C | 全自动从 DB 生成且免审 | 噪声与泄漏风险高 |
| 理由 | 用户明确有结构化会话且可提供连接；seed 保证无 DB 时也能跑通 harness。 |
| 拒绝 B/C | B 不利用资产；C 质量不可控 |

**抽取原则（筛选，非全量）**：

1. 会话含明确编程意图（实现/修复/重构/解释后改代码）
2. 有可恢复的 workspace 信号或最终代码产物（或可构造最小复现仓）
3. 脱敏：剥离密钥、内网 URL、个人信息
4. 去重：按任务指纹（目标摘要 + 文件集合）去重
5. 难度与类型标签：`bugfix` / `feature` / `refactor` / `explain_to_edit` / `test_gen`
6. **禁止**把未脱敏的原始全量对话直接当公开 case

### 决策 3：Agent 架构 — 适配器协议 + 参考实现

| 问题 | 如何「替换不同 Agent 和模型」。 |
|------|------|
| 选项 A | **`AgentAdapter` 接口 + 配置注册表**；首版 `mock` + `openai_compat` | **选中** |
| 选项 B | 硬编码单一 mini-swe-agent | 与「可替换」目标冲突 |
| 选项 C | 仅 subprocess 调任意 CLI，无结构化轨迹 | 难以填 Step/Token/工具字段 |
| 理由 | 接口稳定后可加 mini-swe-agent、OpenCode 等；模型通过 `ModelConfig` 注入，与 Agent 解耦。 |
| 拒绝 B/C | B 锁死；C 结果表大量字段空且难归因 |

**接口契约（逻辑）**：

```text
AgentAdapter.run(case, model_config, budget) -> AgentRunResult
  AgentRunResult:
    status: completed | failed | timeout | infra_error
    artifacts: { files_written, patch, final_message, ... }
    usage: { prompt_tokens, completion_tokens, total_tokens, model_calls }
    steps: [ {step_index, action, tool, duration_ms, ...} ]
    wall_time_s: float
    raw_trace_path: optional
```

### 决策 4：运行与落盘 — 对齐 HTML 推荐目录

| 问题 | 如何保证结果可归档、可生成两张表。 |
|------|------|
| 选项 A | **`runs/<benchmark>__<timestamp>_<run_id>/`** 标准布局 | **选中** |
| 选项 B | 仅打印终端 summary | 无法实验归档 |
| 理由 | 与 `agentic-scaling-benchmark.html` 第八节一致；`summary.json` 是表生成唯一真相源。 |

必产文件（首版最小集，对应落地建议第一阶段）：

- `run_manifest.json` — 实验标识、算法、Agent、模型、预算、case set
- `summary.json` — 聚合指标（驱动两张表）
- `results.jsonl` — 每 case 一行
- `report.md` — 人类可读
- `cases/<case_id>/result.json` — 明细

`attempts.jsonl` / `model_calls.jsonl` 等：首版若 Agent 提供则写，否则省略（不伪造）。

### 决策 5：判分器分层

| 层 | 何时用 | 输出 |
|----|--------|------|
| L0 基础设施 | 沙箱/Agent 崩溃 | `infra_error`，不计入有效 case 分母（与表「有效 Case 数」一致） |
| L1 产物存在性 | 要求产出 patch/文件 | 无产物 → 失败 |
| L2 确定性脚本 | case 带 `grader.command` 或测试 | exit 0 → pass |
| L3 金标对比 | case 带 `gold`（文件树或关键片段） | 规则匹配 / diff 阈值 |
| L4 LLM-judge | 无可靠脚本时 | score ∈ [0,1]，≥ threshold → pass；同时记 `mean_rating` |

Case 声明 `grader.mode`: `script` | `gold` | `llm_judge` | `composite`。

### 决策 6：与通用表的指标映射

| 通用字段 | AI-Coding-Assist 填法 |
|----------|----------------------|
| Benchmark 名称 | `AI-Coding-Assist` |
| 评判类型 | `半确定性`（pairwise 子集为 `偏好判分`） |
| 主指标名称 | `task_success_rate` |
| 主指标值 / 成功率 | 同一数值（成功数/有效 Case 数） |
| 成功数 | grader passed 数 |
| 空 Patch 数 | 要求 patch 但为空的 case 数 |
| mean_rating | L4 judge 均分；无则 null |
| 相对基线胜率 | 仅 pairwise 或显式 baseline 对比 run 时填写 |
| Oracle / 选择命中 | 多 attempt/分支算法接入后填写；单路径 baseline 为 null |
| Token / 时间 / Step | 由 runner 汇总 Agent 回报 |

综述表「主指标」列：名称 `task_success_rate`，值如 `62.0%`。

### 决策 7：配置与一键启动

```bash
# 一键（默认 mock + seed cases）
./scripts/run_benchmark.sh

# 替换 Agent / 模型
./scripts/run_benchmark.sh \
  --agent openai_compat \
  --model-config configs/models/deepseek-pro.yaml \
  --case-set seed-v0 \
  --algorithm baseline
```

配置分层：`configs/agents/*.yaml`、`configs/models/*.yaml`、`configs/runs/*.yaml`（可组合覆盖）。

### 决策 8：预算轴（与 scaling 实验对齐）

首版 runner 支持并写入 manifest：

- `max_steps`（默认 40）
- `max_wall_time_s`（每 case）
- `max_attempts`（默认 1）
- 预留 `branches`（多分支算法适配器自行解释）

预算轴字段：`attempts` | `steps` | `wall_time` | `tokens`（manifest 记录实际使用的轴与值）。

---

## 5. Dependencies and Assumptions

### 5.1 依赖

| 依赖 | 用途 |
|------|------|
| Python 3.11+ / `uv` | 运行时与环境管理 |
| PyYAML、httpx（或等价） | 配置与 OpenAI-compatible API |
| 可选：用户 DB（PostgreSQL/MySQL 等，待确认） | 会话抽取 |
| 可选：Docker | 隔离执行 grader（首版默认可本地 subprocess） |

### 5.2 假设

1. 用户后续可提供 DB 连接与表结构说明；在此之前 seed case 足以验证 harness。
2. 参考 Agent 不保证强 coding 能力；其职责是验证 **协议与报表**，非刷榜。
3. 真实 Agent（mini-swe-agent 等）通过适配器接入，不在首版必须可用。
4. API Key 仅通过环境变量注入（如 `OPENAI_API_KEY` / `AIBENCH_API_KEY`），不入库不进 case。
5. Case 与轨迹默认视为内部数据；公开分享前必须过脱敏清单。

### 5.3 会话数据最小逻辑模型（待 DB 对齐）

抽取层不绑定具体 ORM，假设可映射为：

```text
Session:
  session_id, user_id_hash, created_at, product, language_hints
Message:
  session_id, role, content, timestamp, metadata
Artifact (可选):
  session_id, type(file|patch|command), path, content, accepted:bool
Outcome 信号（可选）:
  user_accepted, followup_needed, explicit_success_flag
```

映射后输出 `case_draft.json`，经审核脚本进入 `benchmarks/ai_coding/cases/`。

---

## 6. Relationship with Existing Designs

- **无 prior `docs/design/*`**。术语与结果口径锚定：
  - `agentic-scaling-benchmark.html`：统一粒度（Run/Case/Attempt/Step/ModelCall）、综述表、通用表、Arena 映射、落盘结构、落地阶段
  - `agentic_scaling_benchmark_tables.md`：列名与字段说明（成功数、Token、时间拆解、错误归因等）
- 本设计将 HTML 中「AI 辅助编程 / 半确定性 / 偏好型」具体化为可运行协议，不修改上述文档的通用列语义。
- **冲突处理**：HTML 偏好型示例主指标为 `win_rate`；本 Benchmark 默认主指标为 `task_success_rate`，在 `summary.json` 中显式写 `primary_metric_name`，综述表按该字段展示，避免与 SWE 的 `resolved_rate` 混名。

---

## 7. Acceptance Criteria

| ID | 标准 | 自动化方式 |
|----|------|------------|
| AC1 | 设计文档含本 8 节且决策含多选项 | 文档审查 |
| AC2 | seed case set ≥ 3，schema 校验通过 | `python -m aibench validate-cases` |
| AC3 | mock agent 一键跑通，exit 0 | `./scripts/run_benchmark.sh` |
| AC4 | 产出 `run_manifest.json`、`summary.json`、`results.jsonl`、`report.md` | 路径存在性检查 |
| AC5 | `summary.json` 含通用表所需核心键：run_id、benchmark_name、case_count、effective_case_count、success_count、success_rate、total_tokens、total_wall_time_h、agent_name、main_model、algorithm_name | `python -m aibench check-summary <run_dir>` |
| AC6 | 导出 Markdown 综述表至少 1 行 + 通用表关键字段块 | 检查 `report.md` 章节标题 |
| AC7 | 切换 `--agent mock` 与配置中的 model 名写入 manifest | 断言 manifest 字段 |
| AC8 | 单元测试覆盖：case 加载、判分、summary 聚合 | `pytest tests/ -q` |
| AC9 | 会话抽取模块在无 DB 时提供 CLI 说明与 dry 接口；有样例映射函数可单测 | `pytest tests/test_extract.py -q` |

---

## 8. Risks and Rollback

| 风险 | 影响 | 缓解 | 回滚 |
|------|------|------|------|
| 会话隐私与密钥泄漏 | 合规事故 | 脱敏清单；默认不提交原始会话；case 仅存必要上下文 | 删除违规 case 与 runs；轮换密钥 |
| 半确定性定义过松/过严 | 指标失真 | case 级 grader 声明；分层 L1–L4；文档化成功定义 | 调整 threshold / 剔除劣质 case，不改历史 run_id |
| LLM-judge 方差 | 不可复现 | 固定 judge 模型与 temperature=0；记录 judge 版本 | 关闭 L4，仅 script/gold |
| Agent 适配过拟合 mock | 真 Agent 接入困难 | 接口字段最小且与轨迹落盘解耦 | 增加适配器，不改 case schema |
| DB schema 与假设不符 | 抽取失败 | 抽取层可配置 SQL/映射；先拿样例 export | 仅用 seed case |
| 结果表字段过多首版填不满 | 报告空洞 | 允许 null；禁止编造数值 | N/A |

**Rollback 总策略**：Benchmark 数据与代码分离；任意实验以 `run_id` 为不可变快照；协议不兼容时新增 `case_schema_version`，旧 run 只读不改写。

---

## 附录 A：Case 逻辑 schema（摘要）

```yaml
case_id: str
schema_version: "0.1"
task_type: bugfix | feature | refactor | explain_to_edit | test_gen | pairwise
language: python  # etc.
prompt: str                    # 用户任务（可含多轮摘要）
context:
  files: [{path, content}]     # 最小工作区快照
  notes: str?
grader:
  mode: script | gold | llm_judge | composite
  command: str?                # e.g. "pytest -q"
  gold_files: [{path, content}]?
  judge_rubric: str?
  judge_threshold: float?      # default 0.7
metadata:
  source: seed | session_derived
  source_session_id: str?      # 仅内部
  difficulty: easy | medium | hard
  tags: [str]
  split: dev | test
```

## 附录 B：推荐实验行语义（与项目一致）

> 一行结果 = 同一 Benchmark + 同一 case set + 一个算法配置 + 一个预算档位 + 一次 run（或多种子聚合）。

Baseline、投机、MoA 等均产出独立 `run_id` 行，进入同一通用结果总表比较。

## 附录 C：待用户提供的信息（阻塞抽取，不阻塞 harness）

1. DB 类型、连接方式、只读账号  
2. 会话 / 消息 / 产物表结构或样例 export（JSON/CSV）  
3. 「成功/采纳」业务信号字段含义  
4. 首批希望覆盖的语言与产品线  

---

## 假设记录（用户未回答澄清题时的默认）

1. 半确定性主路径 + 预留 pairwise  
2. seed 先跑通，DB 抽取接口就绪待接  
3. 可插拔 Agent + mock/openai_compat 参考实现  
4. 结果严格对齐已有两份结果表文档的字段语义  
