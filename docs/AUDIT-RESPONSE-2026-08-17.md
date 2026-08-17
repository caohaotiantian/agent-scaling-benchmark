# 对 `docs/AUDIT-2026-08-17.md` 的回应

**分支** `fix/audit-2026-08-17`，基线 `982a9c41af9877ae3ac637c7a471de655278d76a`。
十三次提交，测试从 713 增至 940，全部门禁绿。

本文引用的 `runs/*` 目录都是**本地产物，未随仓库分发**（`runs/` 已 gitignore），
clone 里不存在；写出目录名是为了让还留着这些产物的机器可以复核。

这份文件是**跟踪入库**的，理由和审计本身入库的理由一样：完整计划与证据在
`.agent/audit-2026-08-17-fixes/plan.md`，而 `.agent/` 是 gitignore 的 —— 那正是 RP-24
指出的毛病。凡是 clone 的人需要知道的结论，都在这里，不在那里。

---

## 一、对已发布提交信息中数字的更正

提交信息改不了，所以更正记在这里。每一条都重新量过。

| 出处 | 原文 | 实测 |
|---|---|---|
| `ede822f` | `python_executable` / `working_directory` 在**全部 148 份** manifest 里 | **74 份**带这两个字段（磁盘上共 242 份）。148 = 74 × 2 个字段 |
| `ede822f` | RP-11「**全部 68 份**已发布 manifest」记着那个字面量 | **90 份**带 `python_version`，全是 `3.13.12` |
| `4e0d054` | 「`grader timeout` 在 218 份 `results.jsonl` 里出现 0 次，对比 **1,454** 次 agent 侧 infra 错误」 | 0 次是准的。1,454 是对**整棵 `runs/` 树**所有产物类型 grep 出来的，不是这 218 份文件：那些文件里是 **482** 条 case 行 / 479 次 attempt 带 `infra_error`。一句话里两个分母 |
| `4e0d054` | RP-13「**全部 16 次** `calibration_*`」记着那个字面量 | **16 次里的 15 次**。`runs/calibration_20260805_144042` 根本没有 manifest |
| `6f2980b` | RP-23「复现了审计的数字」 | token 总数吻合到 0.001%（244,933,739 对 244,931,819）。**文件数不吻合**：234 对审计的 218，今天是 238 —— 多出来的是本分支自己跑的 e2e dry-run。同一分支在两处对同一类产物写了 218 和 234 |
| `6f2980b` | RP-22「在三份文档共 11 处变红，**与审计预测完全一致**」 | 11 处 / 3 份是准的。审计预测的是 **16**；门禁找到 15 处（含审计文档自身的 4 处）。「完全一致」不是事实 |

## 二、审计非目标（需要 owner 决定，本分支不动）

RP-01、RP-02、RP-03、RP-06、RP-08、RP-14、RP-15（公开发布的取舍）·
RP-05 的策略半边 · RP-13 的回填 · RP-24 的 `.gitignore` 决定 · RP-54 的重算 ·
RP-57（`git gc --prune=now` 重写对象库）· RP-04 的许可证**选择**（已放 `LICENSE`，保留全部权利）。

**RP-19 / RP-48 只做了向前生效，这是有意的。** 审计说回填「纯元数据、无代码、约 15 分钟」。
在这里做不到可验证：`auto-v0_3anchor_20260805.json` 指向的用例集是 gitignore 的、本机没有，
`has_reference` 与 `by_anchor_attempts` 只能**断言**而不能**测量**。把一个无法核实的值写进
已发布的测量文件，正是这份审计通篇在讲的那个失败。`calibrations/README.md` 已写明旧导出没有
这两个字段。还留着 `auto-v0` 的机器上可以补。

**RP-57 的现状**：`git fsck --unreachable` 仍能看到重写前那两个 451 字节、匹配 `openai_sk`
的 blob。**不可达，未发布** —— 但那把 key 无论如何都应按已泄露处理，那是轮换决定，不是仓库决定。

## 三、审计判为 low 且实测无暴露面的条目

以下按审计自己的收窄结论记为「不改」，收窄本身就是处置：

M3（零条已发布用例受影响）· M7（13 次校准里全部 236 条 null-`r_pb` 用例都已被丢弃，触发条件不可达）·
M9（回填不是被修的那个 bug；顺序依赖不复现；默认路径不传配额）· M12（默认值不静默，`load_yaml`
会指名报错，且都写进 manifest）· M13（`grouping` 有两处渲染，所有配置都设 `task_type`）·
M17（tier 遗漏已被补偿；role 遗漏在任何已发布锚点面板下都产生不了过期 `p_hat`）·
M18（`sess-`/`db-` 前缀不可能碰撞，磁盘上零次）· M21（每份草稿只有一个 worker 访问，无竞态）·
M22（空 patch 行照判、照失败、留在分母里，这是正确的 T5 处理）· M23（3,900 条已发布用例里零条
设置这些字段）· M26（`key_alias` 到不了导出包）· M27（生产矩阵是 3 组比较不是 4 组，族误差 ≈0.14）。

M15 的收窄版本成立（`llm_judge` 是 env-only）。判分结果现在会写出 `judge=<model>` —— 让这条
环境侧信道在产物里看得见。把 judge 改成配置优先是评测契约的变更，属于 owner。

## 四、审计没发现、写测试时发现的两个缺陷

* `ShellAgent` 的 `(proc.stdout or "")[-2000]` 是**下标不是切片**。任何输出短于 2000 字节的
  CLI 都会让它抛 `IndexError` —— 这个适配器从来没有成功完成过一次跑测。
* 同一适配器的 `empty_patch` 永远不可能为真：它把工作区里每个文件都算作「被写过」。

## 五、`SESSION-2026-08-14.md` §5.4 那个会删 worktree 的测试：已定位

病因是 `materialize_workspace` 一上来就删除目标目录。用 `git archive` 导出 `982a9c4` 后削到
只剩一个 `clamp.py` 复现，已加 `assert_disposable` 守卫。`tests/conftest.py::guard_the_checkout`
抓的是**复发**，不是病因 —— 两者不要混为一谈。

## 六、一处非计划内的改动，交给 owner 决定

`uv.lock` 的包索引从 `pypi.tuna.tsinghua.edu.cn` 变成了 `pypi.org` / `files.pythonhosted.org`
（2,214 行）。这是固定环境那次提交里 `uv` 重新求解的副作用，不是任何一条审计发现要求的。

**实测：只有索引地址变了。** 包名、版本、**哈希**三者在两个方向上完全一致；只有 `cycler`
多出 `size` 与 `upload-time` 两个字段——镜像没提供它们。也就是说装出来的字节完全相同。

保留的理由是审计 Part A 本身：它问的是「另一个人能不能 clone 到一个干净环境里复现」，而锁文件
里写着一个区域镜像，对那张网之外的人答案是不能。但这动到的是 owner 的基础设施选择，所以写在这里
而不是等人发现。要退回：`git checkout 982a9c4 -- uv.lock`。

## 七、门禁

每次提交都跑：`ruff format --check` + `ruff check`（src / tests / scripts）·`pytest`（940 条）·
`scripts/e2e_pipeline.sh --dry-run` ·`scripts/check_doc_links.py`（23 份文档 0 问题）·
`scripts/build_docs_html.py` 后 `git diff --exit-code docs/html` ·`aibench doctor` ·
`scripts/instrument_check.py`（现已上 CI）· 对全部入库文件的 secrets 扫描。
