# 现场还原（Workspace Restoration）设计

目标：Benchmark 执行时尽量还原「任务发生时 Agent 面对的代码现场」，使测评可复现、可横向对比、有说服力。

---

## 1. 问题本质

真实会话里 Agent 依赖的往往不只是 `prompt`，还包括：

| 依赖类型 | 例子 | 若不还原的后果 |
|----------|------|----------------|
| 多文件工程 | 被修改文件 + import 的邻居模块 | 无法编译/运行 |
| 测试与脚本 | `tests/`、`pytest.ini` | 无法客观判分 |
| 第三方依赖 | `requirements.txt`、锁文件 | 环境漂移 |
| 外部仓库版本 | monorepo 某 commit | 现场不可复现 |
| 工具链/环境变量 | `PYTHONPATH`、SDK | 假失败 |

会话日志（`llm_chat_records.full_history`）通常**只覆盖 agent 读过/写过的片段**，不是完整机器镜像。因此策略是：

> **最小充分现场（minimal sufficient scene）**，而不是 1:1 克隆开发者笔记本。

---

## 2. 四级还原阶梯

从便携到完整：

```text
L1 inline files     文件内容嵌在 case JSON（当前默认）
L2 snapshot bundle  case-set 旁的目录 / tar / zip 工程快照
L3 git pin          克隆公开/内网仓库到固定 ref（commit/tag）
L4 setup + env      安装依赖、生成代码、设置环境变量
（L5 容器镜像 — 后续可选，首版不做）
```

Case 协议字段：`context.workspace`（见 `case.schema.json`）。

### 2.1 L1 Inline

```json
"context": {
  "files": [
    {"path": "src/foo.py", "content": "..."},
    {"path": "tests/test_foo.py", "content": "..."}
  ]
}
```

- **何时用**：小任务、教学 seed、依赖 ≤ 数个文件  
- **优点**：单文件可审、无网络、diff 友好  
- **缺点**：大仓不现实；JSON 膨胀

### 2.2 L2 Snapshot

```json
"context": {
  "files": [],
  "workspace": {
    "mode": "snapshot",
    "snapshot": { "path": "snapshots/my_proj" }
  }
}
```

路径解析顺序：

1. `case_set_dir / path`
2. `case_set_dir / snapshots / path`
3. 绝对路径

支持：目录、`.tar` / `.tar.gz` / `.zip`。

- **何时用**：中等工程、需完整目录树但不宜进 git 大 blob 时用 tar（仍可版本管理小目录）  
- **优点**：离线可复现  
- **注意**：敏感数据脱敏；大快照考虑 Git LFS 或对象存储 URL（后续）

### 2.3 L3 Git pin

```json
"workspace": {
  "mode": "git",
  "git": {
    "url": "https://gitcode.com/org/repo.git",
    "ref": "a1b2c3d4...",
    "subdir": "packages/core",
    "sparse_paths": ["packages/core", "tests/core"]
  },
  "strict": true
}
```

执行时 clone → checkout →（可选 sparse）→ 拷贝到 workspace（去掉 `.git`），并写入 `.aibench_git_pin` 记录 resolved SHA。

- **何时用**：开源/可访问内网仓；现场绑定真实 commit  
- **优点**：权威版本、体积小（只存 URL+SHA）  
- **缺点**：需网络与权限；私有仓要 token（环境变量，不入库）

### 2.4 Mixed + overlay（推荐正式 case）

```json
"workspace": {
  "mode": "mixed",
  "snapshot": { "path": "snapshots/calc_project" },
  "setup_commands": ["python -m pip install -q -r requirements.txt"]
},
"files": [
  {"path": "tests/test_x.py", "content": "... 强化后的测试 ..."}
]
```

**应用顺序（固定）：**

1. 清空 workspace  
2. snapshot / git 打底  
3. **inline `files` 覆盖同名路径**（作者可钉死关键文件）  
4. `setup_commands`（cwd=workspace）

示例 seed：`seed-v0-004-snapshot-div`（snapshot 底 + inline 测试覆盖）。

### 2.5 L4 Setup / env

```json
"setup_commands": ["pip install -q pytest"],
"env": { "PYTHONPATH": "." }
```

用于可声明、可审计的环境准备。禁止在 setup 里做不可复现的「随便 curl 最新版」。

---

## 3. 依赖从会话里怎么来？

| 来源 | 抽取方法 | 进入 case 的方式 |
|------|----------|------------------|
| tool `read` 结果 | 解析 `<path>/<content>` | `context.files` 或写入 snapshot |
| tool `grep/glob` 列表 | 得到相关路径清单 | 二次补读（人工/脚本）进 snapshot |
| assistant `write/edit` | 最终文件内容 | gold 或「修复后」对照（慎用，防泄漏答案） |
| 仓库 URL + commit | 日志/metadata/用户消息 | `workspace.git` |
| 本地绝对路径 | 仅内部映射 | **不进公开 case**；改为相对路径快照 |

推荐建 case 流程：

```text
会话筛选
  → 自动草稿（prompt + 已读文件）
  → 人工判定「充分现场」缺什么
  → 补 snapshot 或 git pin
  → 写/收紧 grader（优先 script）
  → 脱敏
  → 正式 case set
```

原则：**Agent 评测时不应再访问原始用户机器**；所有依赖必须经 case 声明的还原通道获得。

---

## 4. 执行期行为（runner）

每个 case：

```text
runs/.../cases/<case_id>/
  workspace/                 # materialize 结果
  workspace_manifest.json    # sources_applied / warnings / setup logs
  result.json
```

- materialize 失败 → `infra_error`（不计入模型能力分母，与设计一致）  
- `results.jsonl` 带 `workspace_sources` 便于审计

实现：`src/aibench/workspace.py` + `runner.py`。

---

## 5. 说服力清单（正式 case 准入）

- [ ] 任务可在还原后的 workspace 独立完成（不依赖会话外隐式状态）  
- [ ] 版本钉死：snapshot 哈希或 git commit 已记录  
- [ ] 判分客观：优先 `grader.mode=script`  
- [ ] 无密钥/内网绝对路径/个人信息  
- [ ] 在干净环境一键 `aibench run` 可复现同结论  
- [ ] 失败归因能区分：现场还原失败 vs 模型失败  

---

## 6. 非目标（首版）

- 完整 IDE / 操作系统镜像  
- 任意二进制大资源无声明拉取  
- 自动从用户磁盘 rsync（隐私与合规）  
- 把原始 `full_history` 整段当公开数据集  

---

## 7. 与 Git 跟踪的关系

- **本仓库 git**：跟踪 harness、schema、seed case、小 snapshot 目录  
- **case 内 git pin**：跟踪「被评测项目」的外部版本，不等于把整仓 submodule 进来  
- 大体量 snapshot：可后续接对象存储 + `snapshot.uri` + 校验和（未实现）
