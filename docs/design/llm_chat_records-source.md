# 数据源：`llm_chat_records`

| 项 | 值 |
|----|-----|
| 库 | `opencsitool_db` |
| 表 | `llm_chat_records` |
| 规模（采样时） | ~18.5k 行 |
| 时间范围 | 2026-04-02 ~ 2026-05-12 |
| 连接 | 仅环境变量 `AIBENCH_DB_URL`（禁止入库） |

## 字段

| 列 | 类型 | 说明 |
|----|------|------|
| `request_id` | varchar(100) PK | LiteLLM 请求 ID（`chatcmpl-...`） |
| `start_time` | datetime | 对话开始时间（索引） |
| `model` | varchar(100) | 主模型（如 `glm-5`, `qwen3.6-plus`） |
| `requests_tags` | text | 标签列表字符串，含 Credential / User-Agent |
| `tools` | json | 挂载工具定义数组 |
| `full_history` | json | OpenAI 风格 messages（system/user/assistant/tool） |
| `created_at` | datetime | 入库时间 |
| `key_alias` | varchar(255) | 调用方标识 |

## 产品分布（近期样本）

- 大量 **opencode**（AI 编程 CLI agent）：tools 含 `bash/read/write/edit/grep/...`
- 亦有 OpenAI JS/Python SDK、OpenClaw、运维总控等

## 与 Case 的映射

| Case 字段 | 来源 |
|-----------|------|
| `case_id` | `db-{request_id短}-{fingerprint}` |
| `prompt` | `full_history` 中主 user 意图（去 system-reminder） |
| `context.files` | tool 结果中 `<path>/<content>` 文件快照 |
| `grader.gold_files` | assistant 回复中的代码块（若有） |
| `metadata.source_session_id` | `request_id` |
| `metadata.source_model` | `model` |

## 抽取命令

```bash
export AIBENCH_DB_URL='mysql+pymysql://USER:PASS@HOST:3306/opencsitool_db?charset=utf8mb4'
uv run python -m aibench extract-from-db \
  --output-dir benchmarks/ai_coding/cases/drafts-from-db \
  --limit 400 --max-cases 30 --require-gold
```

## 质量注意

1. 一条 `request_id` ≈ 一次 LLM 请求快照，不等于完整用户会话线程；长任务会拆成多条。
2. 自动草稿噪声高（巡检、总结、计划模式 prompt 污染），**必须人工筛选** 再进正式 case set。
3. 路径/日志可能含内网主机与个人目录，发布前脱敏。
4. 凭据切勿写入 git；若已在聊天中暴露，建议轮换 DB 密码。
