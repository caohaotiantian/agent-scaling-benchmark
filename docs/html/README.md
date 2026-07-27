# docs/html — 统一 HTML 文档站

本目录是 **AI-Coding-Assist Benchmark** 相关文档的统一展示归档。

## 打开方式

在浏览器中打开：

```text
docs/html/index.html
```

或：

```bash
open docs/html/index.html
```

## 页面列表

| 文件 | 内容 |
|------|------|
| `index.html` | 文档站首页 |
| `project-overview.html` | 项目介绍演示 |
| `reference.html` | 参考手册 |
| `user-guide.html` | 用户向导 |
| `agentic-scaling-benchmark.html` | 结果表设计报告 |
| `tables.html` | 字段字典（表名/说明提取 + 多级表头 + 一级列 rowspan） |
| `configs.html` | 生产配置 |
| `remaining-work.html` | 未尽事项 |
| `assets/site.css` | 共享样式 |
| `_src/project-overview.html` | 介绍页可编辑源 |
| `_src/agentic-scaling-benchmark.html` | 设计报告可编辑源 |
| `_src/tables.md` | 字段字典 Markdown 源（伪表头行由构建脚本智能渲染） |

## 字段字典渲染说明

`_src/tables.md` 中每张 Markdown 表的前两行（仅第一列有字）为 **表名** 与 **说明**。构建时会提取为：

```text
h2.table-title  — 表名
p.table-desc    — 说明
table           — 真实表头 + 数据
```

通用结果总表的一级分组表头会合并为 `colspan`；字段说明表的「一级列」相同值会合并为 `rowspan`。

## 重建

修改 Markdown 源或 `_src/*` 后执行：

```bash
uv sync --extra dev
uv run python scripts/build_docs_html.py
```
