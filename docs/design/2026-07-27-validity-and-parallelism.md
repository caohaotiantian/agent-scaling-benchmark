# 测评科学效度与执行并行化

| 字段 | 值 |
|------|-----|
| 状态 | closed |
| Closed-on | 2026-07-27 |
| 日期 | 2026-07-27 |
| slug | `2026-07-27-validity-and-parallelism` |
| 上游 | `2026-07-25-e2e-session-to-benchmark.md`、USER_GUIDE、REMAINING_WORK 生产差距分析 |

---

## 1. Background and Purpose

当前 harness 可端到端出数，但科学效度不足（自动 case 易泄漏/过易/难分层），执行上 case 内仍串行，抽库后生成与筛选并行度不够。本任务补齐 **可机器执行的效度门禁 + 分层统计**，以及 **case/生成/消融可配置并行**。

若不做：消融数字难信，批跑吞吐卡在串行。

---

## 2. Deliverables

- [x] Case 效度审计：`stub 必须 fail`、污染/泄漏检测、难度启发式、指纹去重  
- [x] CLI `audit-cases`；promote 可选强制审计通过  
- [x] 报告：成功率 **Wilson 95% CI**；按 `task_type` / `difficulty` 分层  
- [x] 可复现：case set content fingerprint 写入 manifest  
- [x] Runner case 级并行（`--workers` / run-config `case_workers`）  
- [x] `generate-cases --workers` 并行 LLM/启发式生成  
- [x] 消融并行保留并与 case 并行兼容文档化  
- [x] 测试 + 设计/实现文档 + USER_GUIDE 更新  

---

## 3. Scope Boundary

**在范围：** 机器可验证的效度门禁与分层指标；ThreadPool 并行（I/O/API 友好）。  

**不在范围：** 人工标定难度真值、Docker 沙箱、统计显著性检验全家桶（仅 CI）、分布式集群队列。  

**质量预算：** audit 与并行路径在 mock dry-run 下正确；并行结果与串行在 mock 上 success 集合一致。

---

## 4. Key Design Decisions

### D1 效度门禁 — 机械门禁优先（选中）

| 选项 | 取舍 |
|------|------|
| **A. stub-fail + 污染扫描 + 元数据标签** | **选中**：可自动化、低成本 |
| B. 仅 LLM 审 case | 贵、不稳 |
| C. 仅人工 | 不可规模化 |

门禁：

1. **stub_fail（script）**：materialize 后立刻跑 grader，必须 **不通过**（否则测试过松或答案已在 stub）。  
2. **contamination**：gold/key_lines/明显正解片段是否已在初始 files 中。  
3. **dedup**：fingerprint（prompt+paths）冲突标记。  
4. **difficulty**：启发式 easy/medium/hard（文件数、测试函数数、LOC）。  

### D2 统计 — Wilson CI + 分层（选中）

| 选项 | 取舍 |
|------|------|
| **A. Wilson score 95% CI + by_task_type/by_difficulty** | **选中** |
| B. Bootstrap 默认 | 可后续加；Wilson 足够轻量 |
| C. 仅点估计 | 拒绝 |

### D3 并行 — ThreadPool + per-case 新 Agent（选中）

| 选项 | 取舍 |
|------|------|
| **A. ThreadPoolExecutor，每个 case 独立 create_agent** | **选中**：适配 API I/O |
| B. ProcessPool | 序列化成本高 |
| C. asyncio 全局重写 | 改动面过大 |

默认 `case_workers=1` 保持可复现调试；生产可设 4–8。

### D4 结果一致性

并行仅改变执行顺序；summary 按 `case_id` 排序后聚合，与串行同输入同 mock 结果一致。

---

## 5. Dependencies and Assumptions

- 复用 `materialize_workspace`、`grade_case`、`ThreadPoolExecutor`  
- Agent 实例 **非线程共享**（每任务新建）  
- script grader 可在本地 pytest 执行  

---

## 6. Relationship with Existing Designs

- 扩展 e2e 设计中的 `weak_grader` / promote，不改为自动发布。  
- 并行与 `ablation --parallel` 正交：消融=run 并行；本任务=case/生成并行。  
- ⚠ 不引入 Docker 沙箱（仍为 REMAINING 生产差距）。

---

## 7. Acceptance Criteria

| ID | 标准 | 命令 |
|----|------|------|
| AC1 | 故意「stub 已通过」的 case 被 audit 判 fail | `pytest tests/test_validity.py` |
| AC2 | 污染 key_lines 被检出 | 同上 |
| AC3 | summary 含 confidence_interval 与 stratified | `pytest tests/test_stats_parallel.py` |
| AC4 | mock 上 workers=1 与 workers=2 成功率一致 | 同上 |
| AC5 | 全量测试绿 | `uv run pytest tests/ -q` |

---

## 8. Risks and Rollback

| 风险 | 缓解 |
|------|------|
| 并行竞态写同一 run_dir | 每 case 独立子目录；汇总加锁/主线程合并 |
| stub_fail 误杀无测试 case | 仅 script 模式强制；gold 只做污染检测 |
| 指纹误伤 | 仅报告 duplicate，默认不删 |

Rollback：`case_workers=1`、跳过 audit 门禁（promote `--skip-audit`）。
