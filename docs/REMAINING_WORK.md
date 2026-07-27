# 遗留任务清单

更新时间：已完成一轮「遗留任务」落地（见 commit history）。

## 已完成

### 主路径
- [x] Case 协议 / schema / workspace 还原  
- [x] mock + openai_compat + **tool_loop** + **shell** Agent  
- [x] 会话抽库 → 规则筛选 → LLM/启发式生成 → validate  
- [x] 一键 e2e、消融矩阵、综述表  
- [x] 用户手册  

### 原 P1
- [x] **promote** CLI（人工门控发布到 `prod-v0`）  
- [x] **weak_grader** 消融默认剔除（`--allow-weak-grader` 可关）  
- [x] **失败诊断** 写入 `report.md` / `summary.failure_diagnostics`  
- [x] **secrets-scan** CLI + generate `--secrets-scan`  

### 原 P2
- [x] **LLM 软过滤** `filter-drafts --llm-soft`  
- [x] **snapshot-skeleton** CLI  
- [x] **llm_judge** 判分实现  
- [x] **tool_loop** 多步工具 Agent  
- [x] **shell** 适配器（可包 mini-swe 等 CLI）  
- [x] **并行消融** `--parallel N`  
- [x] **相对基线收益**（`--baseline-experiment` / 矩阵首行）  
- [x] **费用估算**（`AIBENCH_USD_PER_MTOK*`）  

### 原 P3
- [x] validate-cases 默认 `auto-v0`  
- [x] **CSV / XLSX** 导出（`export-ablation` / ablation `--export-csv|--export-xlsx`）  
- [x] **GitHub Actions CI**（pytest + dry-run）  
- [x] 设计/实现文档状态更新  

## 仍可增强（非阻塞）

| 项 | 说明 |
|----|------|
| Git LFS / 远程 snapshot URI | 大体量现场仍建议自管存储 |
| Fusion 时间拆解全字段 | 无 Fusion 链路时保持 null |
| 消融 run 级失败继续/汇总 | 当前任一 run 异常会抛错 |
| promote 交互式审核 UI | CLI 已够用 |
| tool_loop bash 安全策略加严 | 已禁部分链式符号，可再白名单 |
| 真机 CI 连 DB/API | 默认仅 dry-run，密钥不进 CI |

## 明确不做

- 无人审核自动发布正式集  
- Fusion/投机算法本体  
- 完整 IDE / 全量 OS 镜像现场  
