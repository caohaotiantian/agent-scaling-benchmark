# docs/html —— 文档站

由 `scripts/build_docs_html.py` 从 `_src/` 下三个**手写 HTML 片段**构建，共四页：

| 页面 | 源片段 | 内容 |
|------|--------|------|
| `index.html` | 脚本内的 `INDEX_BODY` | 导航与当前状态摘要 |
| `overview.html` | `_src/overview.html` | 背景、流水线架构、反向构造原理、实测分布 |
| `manual.html` | `_src/manual.html` | 环境准备、端到端流程、参数含义、对外分发、故障排查 |
| `reference.html` | `_src/reference.html` | 设计依据、数据格式、门禁规则、已发布校准数据 |

```bash
uv run python scripts/build_docs_html.py
```

## 两件容易踩的事

1. **改 `docs/*.md` 不会改变站点内容。** 早期版本从 Markdown 生成，现在不是了。
   要改站点就改 `_src/*.html`。
2. **构建会删除 7 个旧页面**（`project-overview` / `user-guide` / `configs` / `tables` /
   `agentic-scaling-benchmark` / `remaining-work` / `handoff`）。它们的内容已并入上面四页。
   不要再链接它们 —— `scripts/check_doc_links.py` 会拦住。

## 与 `docs/REFERENCE.md` 的关系

**是两份不同的文档，不是同一份的两种形态。**
`reference.html`（约 300 行）讲设计论证与数据格式；
`docs/REFERENCE.md`（约 1600 行）讲 CLI 全参数、配置字段、Schema、产物映射、FAQ。
查参数去 `.md`，查「为什么这么设计」去这里。

## `_src/` 里的孤儿

`_src/` 有 6 个文件，只有 3 个在用。另外 3 个不参与构建 —— **改它们不会改变站点**：

| 文件 | 大小 | 状况 |
|------|------|------|
| `project-overview.html` | 52 KB | 对应页面已删；内部链接指向更早的布局 |
| `agentic-scaling-benchmark.html` | 29 KB | 同上 |
| `tables.md` | 43 KB | `write_tables_page` 已不再被 `main()` 调用 |

三者内容均**未**并入现在的四页站。保留供查阅，文件头有 `ORPHAN` 标记。
`scripts/check_doc_links.py` 跳过整个 `_src/` —— 活片段的链接会随构建产物一起被检查。
