# 遗留任务清单

> **展示版（推荐）**：[docs/html/remaining-work.html](html/remaining-work.html)

更新时间：2026-08-04（分层区分度用例流水线落地后）。

## 已完成

### 主路径
- [x] Case 协议 / schema / workspace 还原
- [x] mock + openai_compat + **tool_loop** + **shell** Agent
- [x] 会话抽库 → 规则筛选 → LLM/启发式生成 → validate
- [x] 一键 e2e、消融矩阵、综述表
- [x] 用户手册

### 原 P1 / P2 / P3
- [x] promote CLI、weak_grader 默认剔除、失败诊断、secrets-scan
- [x] LLM 软过滤、snapshot-skeleton、llm_judge、tool_loop、shell 适配器
- [x] 并行消融、相对基线收益、费用估算
- [x] CSV / XLSX 导出、GitHub Actions CI

### 科学效度与并行（2026-07-27）
- [x] stub-fail / 污染 / 难度 / 指纹审计（`audit-cases`）
- [x] Wilson CI + 分层成功率、case set fingerprint
- [x] case 级并行、generate 并行、promote `--require-audit`

### 分层区分度（2026-08-04）
- [x] **T1–T5 分层契约** + 机器可检结构不变量（`src/aibench/tiers.py`）
- [x] **trace 过程信号** 挖掘与层级推导（`extract/trace_signals.py`）
- [x] **确定性消毒定级**：去缺陷标记、去题面泄露、拆隐藏测试（`extract/tier_shaping.py`）
- [x] **隐藏测试** `grader.hidden_tests`（判分时注入）
- [x] **防作弊** `grader.protected_paths` + `reward_hack` 归因
- [x] **可解性门禁** `solvability_gate`（参考解必须通过，与 stub-fail 成对）
- [x] **经验校准** `calibrate-cases`（p_hat / spread / point_biserial / flaky）
- [x] **按区分度选题** `select-cases`
- [x] **McNemar 配对显著性检验** + 按 tier 分层的消融报告
- [x] **修复**：agent 的 model 解析改为「配置优先、env 兜底」。此前 `OPENAI_MODEL` 会覆盖
      每行 `model_config`，导致多模型消融静默跑成同一个模型，而报告仍按不同模型标注
- [x] **修复**：`tool_loop` 执行 bash 时 `[-2000]` 写成单字符索引而非切片，输出短于 2000 字符
      即 `IndexError`。多步 agent 从未真正跑通测试 —— **agent 轴此前从未被测量过**。
      修复后实测：14 步（list → read → read → write → pytest → submit），成功率 1.000
- [x] **修复**：全部 case 都 infra_error 的实验，报告曾显示 `0.0% / -100.0pp`（读作能力结论）。
      现加「有效Case / 基础设施失败」列 + 顶部显式警告
- [x] **修复**：`run_benchmark` 为算内容哈希跑了完整审计（每 case 两次 pytest），改为直接算哈希
- [x] **干扰文件反证校验**：`role=distractor` 却出现在参考解里即判违规（`distractor_in_solution`）
- [x] **参考解最小性**：与初始文件完全相同判 error，改动行占比 >60% 告警
- [x] 采样扩展 pass@k / 成本轴（原 P0 全部）
- [x] **评分干扰检测**：conftest / pytest.ini / 跳过标记等绕过 `protected_paths` 的路子
- [x] **`select-cases --tier-quota`**：按层配额选题，避免整批落在同一能力带
- [x] **`plan-sample-size`**：按 McNemar 反推所需题量，可用 `--from-ablation` 实测不一致率
- [x] **增量校准** `calibrate-cases --reuse-from`：case 内容与锚点面板都未变才复用旧结果
- [x] **锚点漂移检测**：`anchor_fingerprint` 计入配置文件**内容**，改了 YAML 里的模型即整体失效
- [x] **消融行级容错**：单行失败不再中止整个矩阵，失败行进 `failed_runs` 并在报告顶部告警；
      全部失败才报错
- [x] **泄露检测 LLM 二审** `audit-cases --llm-disclosure-check`：正则仍是唯一阻断判据，
      LLM 只能加 warn —— 不可用/解析失败/判错都不能单独否掉一条用例

---

## 未做（按优先级，待未来实现）

### ~~P0 — 直接限制当前区分度结论的强度~~（2026-08-04 已完成）

| # | 项 | 落地 |
|---|----|------|
| 1 | **多次采样与 pass@k** | `_run_one_case` 外层做 k 次独立采样，`_aggregate_attempts` 折成一行（保持一 case 一行，下游三个消费者无需改动）。产出 `pass_at_1` / `pass_at_k` / `pass_pow_k` / `selection_hit_rate`，并填上此前硬编码 `None` 的 `oracle_success_count` / `oracle_success_rate`。`success_rate` 口径不变 —— 它是「选择策略实际提交的结果」，`k=1` 时与旧行为逐字节一致 |
| 2 | **采样温度** | `configs/models/glm52-sampling.yaml`（temperature 0.7）+ `configs/runs/passk.yaml`；`max_attempts>1` 且温度为 0 时 runner 告警并写入 `run_manifest.sampling_warning` |
| 3 | **成本轴指标** | `stats.cost_curve` + `budget_quantiles`（档位取自实测 token 分布），进 `summary.cost_curve` 与运行报告；消融报告加 `token_amplification`（相对基线 token 倍数）与「采样扩展与成本」表 |

消融矩阵新增第 4 行 `passk-glm52`（采样轴），与模型轴、Agent 轴并列，每行仍只变一条轴。

### P1 — 分层体系本身的补强

| # | 项 | 现状 | 落地建议 |
|---|----|------|----------|
| 4 | **T4 / T5 真实产出率未知** | 分层 brief 已写，但只在单元测试与小样本上验证过；仓库级用例（多包 + 干扰文件）尚未规模化产出 | 跑一轮 `--tier T4 --min-tier T4`，统计实际达标率，按结果调 brief 或放宽不变量 |
| 7 | **`estimate_difficulty` 冗余** | 已在文档标记 deprecated；**不移除** —— `stratified_by_difficulty` 在已发布的 `summary.json` 里，删除属破坏性变更 | 待下一次 schema 大版本一并清理 |
| 8 | **仅支持 Python** | 分层不变量、测试函数计数、pytest 输出解析均为 Python 中心 | 抽象出 per-language 适配（测试发现、运行命令、通过率解析） |

### P2 — 校准的工程化

| # | 项 | 现状 | 落地建议 |
|---|----|------|----------|

### P3 — 既有遗留（未变）

| 项 | 说明 |
|----|------|
| Bootstrap / 置换检验 | 已有 Wilson CI 与 McNemar 配对检验 |
| Git LFS / 远程 snapshot URI | 大体量现场 |
| Fusion 时间拆解全字段 | 无 Fusion 时 null |
| **Docker/gVisor 沙箱** | 生产安全关键差距：grader 与 tool_loop 的 bash 都在宿主机执行 |
| tool_loop bash 白名单加严 | 已有基础限制 |
| 真机 CI 连 DB/API | 默认 dry-run |

## 明确不做

- 无人审核自动发布正式集
- Fusion/投机算法本体
- 完整 IDE / 全量 OS 镜像现场
- 人工标定难度真值（改由「trace 推导 + 结构不变量 + 经验校准」三段替代）
