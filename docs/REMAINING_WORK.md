# 遗留任务清单

更新时间：以当前仓库实现为准。

## 已完成（主路径）

- [x] Case 协议 / schema / workspace 还原  
- [x] mock + openai_compat Agent，可配置模型  
- [x] 会话抽库 → 规则筛选 → LLM/启发式生成 → validate  
- [x] 一键 `e2e_pipeline.sh`、消融矩阵、综述表多行  
- [x] `.env` 加载；结果 `summary.json` / `tables.json` / `ablation_report.md`  
- [x] 用户手册 `docs/USER_GUIDE.md`  

## 遗留 / 待加强

### P1 — 质量与发布

| 项 | 说明 | 建议 |
|----|------|------|
| **人工发布流程** | `auto-v0` 仅为候选；无 `promote-to-prod` CLI | 增加审核清单 + 复制到 `prod-v0` 命令 |
| **弱 grader 自动剔除** | 设计要求 `weak_grader` 不进严肃消融；代码未强制过滤 | ablation 默认跳过 `weak_grader=true` |
| **LLM 生成稳定性** | 推理模型偶发空 content / 截断 | 监控重试率；可选非 reasoning 模型 |
| **失败 case 诊断报告** | 仅有 results.jsonl | 聚合 fail 原因 Top-N 到 report |
| **脱敏审计** | 规则脱敏，无独立扫描报告 | 生成后跑 secrets scan |

### P2 — 设计中已声明延后

| 项 | 说明 |
|----|------|
| **LLM 软过滤** | 规则之后再用模型二分类 keep/drop |
| **自动 snapshot 骨架** | 从 tool 读路径打 `snapshots/<case>/` |
| **llm_judge 判分** | `grading.py` 仍为 stub |
| **多轮 / 工具循环 Agent** | 现仅单轮 openai_compat JSON 写文件；无 bash/edit 真工具环 |
| **mini-swe-agent 等适配器** | 需新 adapter 注册 |
| **Git LFS / 对象存储 snapshot** | 大体量现场 |
| **并行消融** | 现串行；可加 case 级/run 级并发 |
| **相对基线收益自动算** | 表字段常空；需指定 baseline run_id |
| **通用表全字段** | 时间拆解 / 投机 / 角色用量等多为 null |

### P3 — 工程与体验

| 项 | 说明 |
|----|------|
| `validate-cases` CLI 默认仍是 `seed-v0` | 与生产默认 `auto-v0` 不一致，可改默认或文档再强调 |
| three-loop 文档收尾 F | 设计/实现 doc 可加 `Status: closed` 与 closing-commit |
| 结果导出 Excel | 仅有 MD/JSON，表设计 HTML/Excel 未自动生成 |
| 费用估算 | 有 token 无数金额 |
| CI | 无 GitHub Actions 跑 `pytest` + dry-run |

### 明确不做（边界）

- 自动写入 published 正式集（必须人工）  
- Fusion/投机算法本体  
- 完整 IDE / Docker 全量镜像现场（首版）  

## 建议优先级（若继续迭代）

1. ablation 强制跳过 `weak_grader` + promote CLI  
2. 多步工具 Agent 适配器（更贴近 opencode 会话）  
3. llm_judge / 人工发布工作流  
4. 并行消融与 CI  
