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

---

## 未做（按优先级，待未来实现）

### P0 — 直接限制当前区分度结论的强度

| # | 项 | 现状 | 为什么重要 | 落地建议 |
|---|----|------|------------|----------|
| 1 | **多分支执行与 pass@k** | `RunConfig.branches` / `max_attempts` / `selection_strategy` 只写进 manifest，`runner` 从不使用；`report.py` 的 `oracle_success_rate` / `selection_hit_rate` 硬编码 `None` | `pass@k − pass@1` 正是 agent「重试 / 多分支」能吃到的收益，是区分 agent 脚手架的核心指标，现在完全测不出来 | 在 `_run_one_case` 外层加 k 次独立采样，产出 `pass@1`(均值) / `pass@k`(至少一次) / `pass^k`(全部)；填上已声明的 oracle 字段 |
| 2 | **采样温度** | 所有 `configs/models/*.yaml` 均 `temperature: 0` | 温度为 0 时重复采样退化为同一个样本，`--repeats` 只能发现环境抖动，发现不了模型自身的方差；pass@k 也失去意义 | 校准与 pass@k 场景用 `temperature 0.2~0.7` 的独立模型配置；保留 0 温配置用于可复现的单次跑测 |
| 3 | **成本轴指标** | 只有总 token / 总耗时 | 同准确率下 token 更省的组合更强，这是拉开 agent 差距的第二维度 | 输出 `success_rate @ token budget` 曲线与 `token_amplification` |

### P1 — 分层体系本身的补强

| # | 项 | 现状 | 落地建议 |
|---|----|------|----------|
| 4 | **T4 / T5 真实产出率未知** | 分层 brief 已写，但只在单元测试与小样本上验证过；仓库级用例（多包 + 干扰文件）尚未规模化产出 | 跑一轮 `--tier T4 --min-tier T4`，统计实际达标率，按结果调 brief 或放宽不变量 |
| 5 | **干扰文件无法校验** | `role: distractor` 完全由生成器声明，没有任何机制确认它「确实与解无关」 | 用参考解 diff 校验：被声明为 distractor 的文件若出现在 gold_files 中即判违规 |
| 6 | **参考解未校验最小性** | `solvability_gate` 只验「参考解能过」，不验「改动最小」 | 加最小性检查：参考解触及的文件数/行数超过阈值时告警 |
| 7 | **`estimate_difficulty` 冗余** | easy/medium/hard 旧口径与 `tier` 并存，前者已被证明退化（93.8% 判 medium） | 报告与分层统一切到 `tier`，`difficulty` 标记为 deprecated 后移除 |
| 8 | **仅支持 Python** | 分层不变量、测试函数计数、pytest 输出解析均为 Python 中心 | 抽象出 per-language 适配（测试发现、运行命令、通过率解析） |
| 9 | **泄露检测为正则** | `find_disclosures` 双语正则，保守过判优于漏判，但仍会漏掉改写过的泄露 | 在 audit 阶段补一次 LLM 判定作为二审 |

### P2 — 校准的工程化

| # | 项 | 现状 | 落地建议 |
|---|----|------|----------|
| 10 | **校准无增量/断点续跑** | 每次 `calibrate-cases` 全量重跑，成本 = 锚点数 × repeats × 全集 | 按 case fingerprint 缓存历史结果，只跑新增/变更的 case |
| 11 | **锚点漂移无策略** | 校准结论绑定当时的锚点面板；模型升级后 `p_hat` 失效，但没有失效标记 | `calibration.json` 记录锚点指纹；case 元数据带上校准时间与锚点版本，过期即提示重校准 |
| 12 | **样本量规划无工具** | 「要分辨 10pp 需要多少题」只在文档里给了估算 | 加 `plan-sample-size` 子命令：给定目标效应量与 p 分布，反推所需题量 |
| 13 | **`select-cases` 不做分层配额** | 纯按 `spread` / `r_pb` 降序取前 N，可能全部集中在某一层 | 增加 `--tier-quota T2=0.3,T3=0.4,...`，在保证区分度的同时保持层级覆盖 |
| 14 | **反作弊面偏窄** | 只覆盖 `protected_paths` 字节比对 | 补：`conftest.py` 注入检测、`sys.modules` 猴补丁检测、跳过标记（`pytest.mark.skip`）检测 |

### P3 — 既有遗留（未变）

| 项 | 说明 |
|----|------|
| Bootstrap / 置换检验 | 已有 Wilson CI 与 McNemar 配对检验 |
| Git LFS / 远程 snapshot URI | 大体量现场 |
| Fusion 时间拆解全字段 | 无 Fusion 时 null |
| 消融 run 级失败继续/汇总 | 当前异常即中断 |
| **Docker/gVisor 沙箱** | 生产安全关键差距：grader 与 tool_loop 的 bash 都在宿主机执行 |
| tool_loop bash 白名单加严 | 已有基础限制 |
| 真机 CI 连 DB/API | 默认 dry-run |

## 明确不做

- 无人审核自动发布正式集
- Fusion/投机算法本体
- 完整 IDE / 全量 OS 镜像现场
- 人工标定难度真值（改由「trace 推导 + 结构不变量 + 经验校准」三段替代）
