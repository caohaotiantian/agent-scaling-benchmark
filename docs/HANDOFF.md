# 交接文档：分层区分度 Benchmark

分支 `feat/tiered-discrimination-benchmark`，26 commits，65 文件，+7819/-244，231 项测试全绿。
基线 `4898b36`。

---

## 1. 要解决的问题

原有 benchmark 无法区分参与测试的 model+agent 组合。实测基线：

- 成功率 87.5%（56/64），Wilson CI 宽 16.3pp
- 64 个 case 里 **60 个只用了 2 步**，平均 1418 token、17.5 秒
- 文件数恒为 2，31/64 的 stub 里直接写着 `# BUG:` 注释

天花板效应下，任意两个组合在数学上不可分。原因是结构性的，不是阈值没调好：生成端强制"最小自包含单点缺陷"，门禁只有下界（stub 必 fail）没有上界，主指标是单次二值 pass/fail。

---

## 2. 已交付

### 2.1 分层契约（`src/aibench/tiers.py`）

用例按「结构上强迫求解者做什么」分层，每层配机器可检不变量：

| 层 | 能力轴 | 关键不变量 |
|----|--------|-----------|
| T1 直接修复 | — | ≤3 文件，允许题面/注释给出缺陷位置（地板锚） |
| T2 定位修复 | A1 | 题面无机制泄露，stub 无 BUG 标记 |
| T3 隐藏规格 | A1,A5,A6 | ≥2 隐藏测试函数 + 参考解 + 保护可见测试 |
| T4 上下文检索 | A1,A2,A5,A6 | ≥5 文件 + ≥1 干扰文件 |
| T5 跨文件自修复 | A1–A6 | 参考解触及 ≥2 文件 + ≥4 隐藏测试函数 |

层级由**源 trace 的过程信号**推导（`extract/trace_signals.py`）：检索次数、触及文件数、测试运行次数、`test→edit→test` 修复轮次。分布因此继承真实生产任务的难度构成。

`settle_tier` 从 T5 逐层下探，**标签描述产物而非请求** —— 材料只够 T3 就标 T3，绝不虚标。

### 2.2 消灭捷径

| 机制 | 作用 |
|------|------|
| `grader.hidden_tests` | agent 结束后才注入工作区，消灭"读测试反推答案" |
| `grader.protected_paths` | 判分前比对字节，改测试判 `reward_hack` |
| 评分干扰检测 | 挡住绕过路径：注入 conftest 猴补丁、pytest.ini deselect、新文件塞 skip 标记 |
| `solvability_gate` | **参考解必须通过**，与 stub-fail 成对构成难度上下界 |

### 2.3 度量

- **pass@k**：k 次独立采样折成一行落盘（保持一 case 一行，下游三个消费者无需改动）。产出 `pass_at_1` / `pass_at_k` / `pass_pow_k` / `selection_hit_rate`。`success_rate` 口径不变，k=1 时与旧行为逐字节一致
- **成本轴**：`cost_curve`（档位取自实测 token 分布）+ `token_amplification`
- **McNemar 配对检验**：同一 case 集上的配对比较，比各自的 Wilson CI 灵敏得多
- **`plan-sample-size`**：按实测不一致率反推所需题量

### 2.4 校准与选题

`calibrate-cases` 用锚点面板实测每条用例的 `p_hat` / `spread` / `point_biserial` / `flaky`，
淘汰送分题、坏题、噪声题；`select-cases --tier-quota` 按层配额组集。

支持增量（`--reuse-from`，case 与面板都未变才复用）与并行（`--parallel`）。

**锚点面板必须能施展被测层级的能力轴** —— agent 用 `capability_axes` 声明，
`calibrate-cases` 开跑前核对，不匹配直接拒绝。

### 2.5 其他

- 多语言：`languages.py` 收拢 per-language 知识，注册 Python(pytest) 与 JavaScript(`node --test`)，**只注册本机能真跑的语言**
- `compose-cases`：从已验证用例合成 T4 检索用例，两道门禁由构造保持
- 消融行级容错、真机 CI（缺 secret 时跳过而非失败）

---

## 3. 修复的既有缺陷

这些**不是本次引入的**，是原本就在、但会让结论失真的问题：

| 缺陷 | 后果 |
|------|------|
| `tool_loop.py` 用 `[-2000]` 单字符索引而非切片 | 一执行 bash 就 IndexError。**多步 agent 从未真正跑通过测试，agent 轴从来没被测量过** |
| agent 的 model 解析 env 优先 | `OPENAI_MODEL` 覆盖每行 `model_config`，多模型消融静默跑成同一模型，报告却按不同模型标注 |
| 全 infra_error 的实验显示 `0.0% / -100.0pp` | 读作能力结论，实为 harness 没跑起来 |
| `run_benchmark` 为算哈希跑完整审计 | 每 case 两次 pytest，校准时 ×anchors×repeats |
| tool_loop bash 只挡元字符 | `rm -rf` / `curl` / `$(...)` 全放行，宿主机上等于任意执行 |
| 消融单行异常中止整个矩阵 | 几小时付费运行被一次网关抖动清零 |

---

## 4. 实测结果

### 4.1 两轮建集对比

| | round 1 | round 2 |
|---|---:|---:|
| 生成条数 | 59 | 126 |
| 带参考解 | 32（54%） | 100（79%） |
| 审计通过 | 59/59\* | 98/126 |

\* round 1 的可解性门禁还是有条件的，27 条无参考解被静默跳过。

### 4.2 首次拿到显著结果（round 2，28 条选出用例）

| 候选 | 仅基线 | 仅候选 | p 值 | 显著 |
|---|---:|---:|---:|---|
| GLM-5.1（模型轴） | 11 | 0 | **0.0010** | **是** |
| tool_loop（agent 轴） | 1 | 2 | 1.0000 | 否 |
| pass@k=5（采样轴） | 0 | 2 | 0.5000 | 否 |

模型轴 85.2% vs 44.4%。round 1 同样两个模型是 92.3% vs 76.9%、p=0.5 不显著。
**同样的模型，在更好的用例集上差距从 15pp 拉到 40.7pp。**

### 4.3 成本轴

| 实验 | 成功率 | pass@1 | pass@k | token 倍数 |
|---|---:|---:|---:|---:|
| 基线 GLM-5.2 | 85.2% | 85.2% | — | 1.00x |
| tool_loop | 88.9% | 88.9% | — | **29.81x** |
| 采样 k=5 | 92.6% | 80.0% | 92.6% | 4.44x |

tool_loop 花 30 倍 token 换 +3.7pp；重复采样花 4.4 倍换 +7.4pp。

### 4.4 并行

| | case-runs | 耗时 | 单位耗时 |
|---|---:|---:|---:|
| 串行 | 354 | 203 分钟 | 34.4s |
| 并行（15 并发） | 756 | **76 分钟** | **6.0s** |

网关并发探针：1→16 并发，延迟 4.7s→5.0s，吞吐 0.21→2.74/s。
**注意：这是特定时刻的观测。后续跑测已出现 429 限流，容量随时间变化。**

---

## 5. 经验教训

### 5.1 最重要的一条：静默的错误结论比崩溃危险得多

本次修的缺陷里，**没有一个会让程序崩溃**。它们都产出了看起来完全正常的数字：

- model 解析 bug → 报告如实写着"GLM-5.1 vs GLM-5.2"，实际跑的是同一个模型
- tool_loop 切片 bug → agent 轴数据一直存在，只是全部是 infra_error
- 全失败实验 → 显示 `0.0%`，读起来像"这个 agent 很差"

判据：**任何"沉默地给出错误答案"的路径，优先级高于"大声崩溃"的路径。**

### 5.2 我自己犯过三次同类错误：对比被污染

| # | 错误 | 后果 |
|---|------|------|
| 1 | 拿 27 条**筛选后**用例对比 126 条全量 | 合成效果看起来是大成功 |
| 2 | 把无参考解的**坏题**混进"难题"组 | 得出"修复跨度 <15% 是主因"，差点据此加错门禁 |
| 3 | 用**测不了该能力**的面板读 T4 数据 | 弱锚点通过率反而 +24pp，看起来像好消息 |

三次的数字表面都说得通，**只有回到机制才发现问题**。教训：任何对比先问"两组除了我关心的变量，还差什么"。

### 5.3 豁免条款是缺陷的藏身处

`check_reference_solution` 有一行"没有参考解就跳过"。结果：**最没能力自证可解的用例，恰恰是唯一没被要求自证的**。18 条无人能解的用例里 16 条走了这条豁免。

同类：`generate_case` 拒绝无参考解的输出后，调用方回退到启发式路径，而它造不出参考解 —— 于是安静地产出了刚被拒的东西。

### 5.4 同一个守卫抄三份，必然漏一份

`materialize_workspace` 和隐藏测试写入都有路径逃逸防护，`check_reference_solution` 没有。
生成的用例带 `/home/code/...` 绝对路径，参考解被写到**宿主机文件系统**上。已收进 `workspace.safe_relpath`。

### 5.5 能力定义与度量工具必须匹配

T4 测检索，但单轮 agent 把整个工作区贴进 prompt —— 对它而言没有"检索"这回事，干扰文件是白送的可用代码。
**施展不了某条轴的成员不会在那条轴上得低分，它得的是另一回事的分。**

已做成硬检查（`capability_axes` + `unfit_anchors`），因为这类错误从数字上看不出来。

### 5.6 端到端测试发现单元测试发现不了的

JavaScript 那条端到端用例逼出两个 bug：隐藏测试文件名 `clamp.test_spec.mjs` 不匹配 node 的发现规则（静默不跑）、tally 正则只认 TAP 的 `# pass` 而 node 默认输出 `ℹ pass`。纯 Python 单测覆盖不到。

---

## 6. 当前状态与未尽事项

### 6.1 最大瓶颈：送分题占 63%

round 2 的 p_hat 分布仍是双峰：

| p_hat | 占比 |
|---|---:|
| <0.2 无人能解 | 24% |
| 0.2–0.8 中段 | 13% |
| >0.8 人人能解 | 63% |

强制参考解修掉的是"无人能解"那一侧。送分题那一侧**没有找到结构性杠杆**：

| 因素 | 是 | 否 |
|---|---:|---:|
| 修复 <15% 行 | 78.1% | 70.4% |
| LOC < 80 | 76.5% | 73.3% |
| 题面给出具体失败样例 | **100%**（n=11） | 73.0% |

前两个差距只有 3–8pp，**筛掉它们救不了这个集合**。结论：任务本身就简单，生成器从真实 trace 出发却总收敛到"某函数边界条件不对"。这需要改生成策略，不是加门禁。

候选方向（未实施）：
1. 要求复用 trace 里的真实文件内容与调用关系，而非"inspired by"式自由发挥
2. 反向构造：从 trace 里 assistant 实际做的多步修改反推缺陷

### 6.2 T5 产出为 0

A4 跨文件一致性（一个缺陷跨 2 文件、只修一处仍失败）生成器造不出来。
强制 T4 跑 10 条时 `too_few_solution_files` 10/10。两条路线：
仓库快照裁剪（`context.workspace` 的 snapshot/git 通路已有但未用起来）、多轮生成。

### 6.3 待完成的对比

「合成是否让用例更难」**仍无干净答案**。之前的配对对比用的是不合规面板，结论不可采信。
正在用检索面板重测 27 条宿主（`disc-v0`），跑完与 `retrieval-v0` 配对即得结论。
结果落在 `runs/calibration_*/calibration.json`。

### 6.4 安全缺口

**Docker/gVisor 隔离未做，是目前最大的安全缺口。** grader 命令与 tool_loop 的 bash 都在宿主机
以 harness 权限执行。bash 白名单只是缓解 —— `python` 在白名单上，它本身就能做任意事。

### 6.5 其他

- `estimate_difficulty`（easy/medium/hard）已废弃但**不移除**：`stratified_by_difficulty` 在已发布的 `summary.json` 里，删除属破坏性变更，待下次 schema 大版本
- 样本量：按 round 2 实测的 19.8% 不一致率，分辨 10pp 需 **153 条**

---

## 7. 下一步建议（按证据排序）

1. **改生成端难度** —— 送分题 63% 是当前最直接的瓶颈，比补 T5 或 Docker 都更限制结论强度。方向见 §6.1，属于生成策略改动而非参数调整
2. **跑完 §6.3 的对比** —— 决定 `compose-cases` 是保留、改造还是废弃
3. **Docker 隔离** —— 安全关键，独立基建工程
4. **T5 生成路线** —— 与 §6.1 的方向 2 是同一件事，可合并考虑

---

## 8. 常用命令

```bash
# 建集
uv run python -m aibench extract-from-db --output-dir <d> --limit 900 --max-cases 400
uv run python -m aibench filter-drafts --input-dir <d> --output-dir <k>
uv run python -m aibench generate-cases --input-dir <k> --output-dir <c> --max-cases 130 \
  --workers 6 --audit --secrets-scan
uv run python -m aibench audit-cases --case-set auto-v0 --annotate

# 校准与选题（并发 ≈ parallel × workers）
uv run python -m aibench calibrate-cases --case-set auto-v0 --repeats 2 --parallel 3 --workers 5
uv run python -m aibench select-cases --calibration <cal>/calibration.json \
  --from-set auto-v0 --to-set disc-v0 --tier-quota "T2=0.3,T3=0.7"

# T4 检索用例（供体取未筛的全量，宿主取筛后的）
uv run python -m aibench compose-cases --from-set disc-v0 --donor-set auto-v0 \
  --to-set retrieval-v0 --target-files 6 --donors-per-case 4
uv run python -m aibench calibrate-cases --case-set retrieval-v0 \
  --anchors configs/runs/anchor-panel-retrieval.yaml --repeats 2 --parallel 3 --workers 5

# 消融与样本量
uv run python -m aibench ablation --matrix configs/runs/ablation-matrix.yaml \
  --case-set disc-v0 --parallel 2 --export-csv
uv run python -m aibench plan-sample-size --delta 10 --from-ablation <abl>/ablation_summary.json

# 门禁
./scripts/lint.sh && uv run pytest tests/ -q
```

权威参数参考见 `docs/REFERENCE.md`（§13.5 分层、§13.6 pass@k 与成本轴、§14 效度门禁）。
