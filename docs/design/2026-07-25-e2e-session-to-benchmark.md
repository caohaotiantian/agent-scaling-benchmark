# E2E：会话数据 → Benchmark 用例 → 消融实验 → 格式化结果

| 字段 | 值 |
|------|-----|
| 状态 | L1-round1-fixed |
| 日期 | 2026-07-25 |
| 任务 slug | `2026-07-25-e2e-session-to-benchmark` |
| 上游 | `docs/design/2026-07-25-ai-coding-benchmark.md`、`workspace-restoration.md`、`llm_chat_records-source.md` |

---

## 1. Background and Purpose

### 1.1 当前状态（已有）

仓库已具备 harness v0.1：

- Case schema、seed-v0、workspace 还原（inline/snapshot/git）
- mock / openai_compat Agent、单次 `aibench run`、结果表字段
- `extract-from-db` 从 `llm_chat_records` 拉草稿（启发式过滤，噪声高）

### 1.2 缺口（本任务）

用户目标是 **端到端可执行闭环**：

```text
原始会话 (MySQL llm_chat_records)
  → 自动识别 / 筛选
  → （可选 AI）生成可验收 benchmark 用例
  → 一键跑测
  → 可配置 Agent × 模型 消融
  → 产出符合 agentic_scaling_benchmark_tables 口径的结果
```

当前缺口：

| 缺口 | 影响 |
|------|------|
| 筛选过松 | 运维巡检、打分会话进入草稿，测评无说服力 |
| 用例生成停在 draft | 无 script grader、无正式 case set 晋升路径 |
| 无 AI 辅助生成测试 | 大量真实任务无法自动变成可执行验收 |
| 无消融编排 | 每次手改配置，无法横向对比多 Agent/模型 |
| 无一键全流程脚本 | 无法演示/复现完整链路 |
| 未统一加载 `.env` | API/DB 配置易漏 |

### 1.3 若不做

- 会话资产无法稳定转化为可复现测评集
- Agentic Scaling 横向对比依赖手工拼装，结果表长期占位
- 消融实验成本高、口径易漂

---

## 2. Deliverables

- [x] **质量筛选器**：规则硬过滤，输出 `kept/dropped` 与理由（LLM 软过滤为 P2 可选）
- [x] **Case 生成器**：heuristic 规范化 + 可选 LLM；schema 校验；优先安全 `script` grader
- [x] **候选集晋升**：draft → filter → generate → 写入 **`auto-v0` 候选集**（非 published；`split=auto`, `review_status=needs_review`）
- [x] **消融 runner**：矩阵 YAML，串行多组 `(agent, model, algorithm)`，独立 `run_id` + 聚合报告
- [x] **结果聚合**：多 run → `ablation_summary.json` + `ablation_report.md`（综述表多行）
- [x] **一键脚本** `scripts/e2e_pipeline.sh`（真源编排；子命令可单步调试）
- [x] **配置**：`.env` 加载；`configs/models/glm52.yaml`；`configs/runs/ablation-matrix.mock.yaml`
- [x] **测试与 AC**：单元 + `./scripts/e2e_pipeline.sh --dry-run`
- [x] **实现文档** `docs/implementation/2026-07-25-e2e-session-to-benchmark.md`

---

## 3. Scope Boundary

### 在范围

| 项 | 说明 |
|----|------|
| 数据源 | `llm_chat_records`（已验证可用） |
| 筛选 | 规则硬过滤 + 可选 LLM 软过滤 |
| 生成 | 优先生成 **自包含 script 验收** 的小 case；失败回退启发式 |
| 候选集 | `auto-v0` / `e2e-demo` = **自动候选**，默认不视为 published |
| 消融 | Agent/模型矩阵，串行 run + 聚合表 |
| 结果 | 子 run 的 `summary.json`/`tables.json` + 聚合 `ablation_report.md` |

### 术语（消解与源文档冲突）

| 术语 | 含义 |
|------|------|
| **draft** | 抽取产物，未校验/未筛选完毕 |
| **auto candidate set** (`auto-v0`) | schema 合法、可被 runner 加载；`review_status=needs_review`；**非发布** |
| **published case set** | 人工 keep 后的正式集（如未来 `prod-v0`）；本任务**不自动写入** |

⚠ 与 `llm_chat_records-source.md`「必须人工筛选再进正式集」一致：本流水线止于 **candidate**，不替代人工发布。

### 不在范围

| 项 | 原因 |
|----|------|
| 自动写入 published case set | 与源文档人工门槛冲突；明确不做 |
| Fusion/投机算法本体 | 仅配置接入点 |
| Docker 全量镜像现场 | 沿用 workspace-restoration 非目标 |
| 默认开启 LLM 软过滤 | P2；CI 默认规则过滤 |
| 并行集群调度 | 串行即可 |
| 修改结果表列语义 | 只填充既有字段 |
| P2：自动 snapshot 骨架 | 延后 |

### 质量预算

| 属性 | 决策 |
|------|------|
| E2E dry-run | mock 路径：不访问外网 LLM 时，`e2e_pipeline.sh --dry-run` exit 0 且产出表文件 |
| 真 API 路径 | 有 `.env` 时 `generate` 至少成功写出 ≥1 合法 case（或明确 0 且 exit 非 0 仅当配置要求 min_cases） |
| 延迟 SLO | **排除**（外部 API 波动） |
| 吞吐 | **排除** |

---

## 4. Key Design Decisions

### D1：流水线形态 — 子命令 + shell 编排（选中）

| 选项 | 说明 | 取舍 |
|------|------|------|
| A. 新增 `aibench pipeline` 单体命令 | 多一层封装 | 拒绝：与 shell 重复 |
| **B. CLI 子命令 + `scripts/e2e_pipeline.sh` 编排** | 子命令可单测；shell 一键 | **选中** |
| C. 仅 shell 无 Python 子命令 | 难测 | 拒绝 |

**真源**：业务逻辑在 Python 子命令；一键入口是 shell（加载 `.env`、串步骤）。

### D2：筛选策略 — 规则优先，LLM 可选（选中）

| 选项 | 说明 |
|------|------|
| **A. 硬规则 drop 噪声 + 可选 LLM 打分** | **选中** |
| B. 仅 LLM | 贵、不稳、无网不可跑 |
| C. 仅规则 | 真实数据噪声形态多，召回/精度两难 |

**硬规则 drop（任一命中即丢）**（可配置）：

- User-Agent 无 coding agent 且无 coding tools
- prompt 匹配运维巡检 / HEARTBEAT / rubric-judge 元评测 / 纯闲聊
- message 过短或无 user
- 指纹重复

**软规则 keep 加分**：有 context 文件、有代码块、编程意图词、opencode。

LLM 软过滤：输入 prompt 摘要，输出 `{keep:bool, reason, task_type}`；`--no-llm-filter` 可关。

### D3：生成契约 — 防泄漏 + schema（选中）

| 选项 | 说明 |
|------|------|
| **A. LLM/启发式生成「任务+初始文件+测试」；禁止把会话 assistant 终态当作唯一 gold 验收** | **选中** |
| B. 直接用 assistant 代码块作 gold | 拒绝：答案泄漏，消融虚高 |
| C. 仅 schema 合法弱 case 进消融 | 拒绝：污染分母 |

规则：

1. **优先** `grader.mode=script`，测试由生成器**新写**（可参考任务描述，但不得要求 agent 输出与会话终态逐字相同）。  
2. 若仅能 gold：标记 `metadata.weak_grader=true`，默认 **不进入** 严肃消融矩阵（demo/heuristic 可用）。  
3. grader.command 白名单：`python -m pytest ...` / `python <file>.py` / `true`；禁 shell 链。  
4. 密钥：`OPENAI_*` 优先，兼容 `AIBENCH_API_KEY`。

### D4：候选集位置 — `auto-v0` 与 seed 分离（选中）

| 选项 | 说明 |
|------|------|
| **A. `cases/auto-v0/` 候选集 + gitignore** | **选中** |
| B. 覆盖 seed-v0 | 污染基线 |
| C. 仅 /tmp | 难调试 |

`seed-v0` 保持手写 published 基线。dry-run 生成 `e2e-demo`（gitignore）；**消融默认 case_set=`seed-v0`**（保证空候选时仍可出表）。真链路可在矩阵中显式 `case_set: auto-v0`。

### D5：消融编排 — 矩阵 YAML（选中）

```yaml
case_set: auto-v0
runs:
  - experiment_name: baseline-mock
    algorithm_name: Baseline
    agent_config: configs/agents/mock.yaml
    model_config: configs/models/mock-model.yaml
  - experiment_name: glm-openai-compat
    algorithm_name: Baseline
    agent_config: configs/agents/openai_compat.yaml
    model_config: configs/models/glm52.yaml
```

| 选项 | 说明 |
|------|------|
| **A. 矩阵 YAML + `aibench ablation`** | **选中** |
| B. 每次手工改 run config | 不可扩展 |
| C. 复杂 DAG 引擎 | 过重 |

每单元格独立 `runs/...` 目录；结束后写 `runs/ablation_<ts>/ablation_summary.json` + `ablation_report.md`（多行综述表）。

### D6：dry-run / 空集策略（选中）

| 选项 | 说明 |
|------|------|
| **A. dry-run：fixture 走 extract/filter/generate；ablation 固定 seed-v0 + mock 矩阵** | **选中** |
| B. dry-run 也依赖 DB | 拒绝 |
| C. 空 auto-v0 时 ablation 失败 | 拒绝：破坏演示 |

产物：`.e2e-artifacts/` + `runs/e2e-dry-run/ablation_*/ablation_report.md`。

### 实施优先级（非决策，执行顺序）

P0：env、规则筛选、生成、消融 mock、e2e dry-run。  
P1：真 DB extract + LLM generate（可选）。  
P2：LLM 软过滤、snapshot 自动骨架。

---

## 5. Dependencies and Assumptions

| 依赖 | 说明 |
|------|------|
| MySQL `AIBENCH_DB_URL` | 已验证 `llm_chat_records` ~18.5k 行 |
| OpenAI-compatible API | `.env`: `OPENAI_BASE_URL`, `OPENAI_API_KEY`, `OPENAI_MODEL`（实测 `/models` 200） |
| 现有模块 | `extract/*`, `runner`, `workspace`, `report`, `agents` |
| Python / uv / pytest | 既有 |

假设：

1. 自动生成 case 默认 `split=auto`，不声称「已人工审核发布」。
2. AI 生成的 script 在隔离 workspace 跑；危险命令仅限 pytest/python 白名单（生成后静态检查）。
3. 消融时 mock 不需要 API；openai_compat 需要 key。
4. 不在 git 提交 `.env`。

---

## 6. Relationship with Existing Designs

| 文档 | 关系 |
|------|------|
| `2026-07-25-ai-coding-benchmark.md` | 父设计：case 协议、半确定性、结果表映射；本任务补 **闭环生成与消融** |
| `workspace-restoration.md` | 生成 case 优先 L1 inline；snapshot 为 P2 |
| `llm_chat_records-source.md` | 表字段与抽取映射；本任务在其上加 filter/generate |
| `agentic_scaling_benchmark_tables.md` | 结果列口径；消融聚合不得改列语义 |

冲突标记：

- 父设计「大规模自动免审上线」为非目标 — **本任务不取消**；只交付 candidate 流水线。  
- ⚠ `llm_chat_records-source.md` 要求人工筛选进正式集 — 本任务 **auto-v0 ≠ 正式集**，需人工晋升才可改名/复制为 published。

---

## 7. Acceptance Criteria

| ID | 标准 | 命令/度量 |
|----|------|-----------|
| AC1 | 噪声 drop、编程 keep | `uv run pytest tests/test_filter.py -q` |
| AC2 | heuristic 生成 schema 合法 | `uv run pytest tests/test_generate.py -q` |
| AC3 | dotenv 加载 KEY=VALUE | `uv run pytest tests/test_env_config.py -q` |
| AC4 | mock 消融 ≥2 runs | `uv run pytest tests/test_ablation.py -q` |
| AC5 | 报告含「项目效果综述表」且 ≥2 数据行 | 同上 test 断言 |
| AC6 | dry-run exit 0 且存在 `ablation_report.md` | `./scripts/e2e_pipeline.sh --dry-run` + 路径存在 |
| AC7 | 全量单测绿 | `uv run pytest tests/ -q` |
| AC8 | 可选真 API 生成 ≥1 case | 有网时；不阻塞 CI |

---

## 8. Risks and Rollback

| 风险 | 缓解 | 回滚 |
|------|------|------|
| 答案泄漏虚高指标 | D3：禁止会话终态作唯一 gold；弱 grader 不进严肃消融 | 只用 seed-v0 |
| LLM 危险命令 | grader 白名单 | `--heuristic-only` |
| API/DB 不可用 | dry-run + seed 消融 | 离线演示 |
| 误杀真实 coding | filter 报告可审 dropped | 放宽规则 |
| 部分消融失败 | 串行；失败不写成功聚合 | 重跑 |
| 密钥泄漏 | `.env` gitignore | 轮换 |
| 候选集被误当 published | 术语/needs_review/gitignore auto-v0 | 不提交 auto-v0 |

Rollback：`--heuristic-only` / `--dry-run` 降级；git revert。

---

## 附录：端到端目标命令（验收形态）

```bash
set -a && source .env && set +a
./scripts/e2e_pipeline.sh --dry-run
# 真链路（短）：
./scripts/e2e_pipeline.sh \
  --limit 50 --max-cases 5 \
  --matrix configs/runs/ablation-matrix.mock.yaml
```
