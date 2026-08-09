#!/usr/bin/env python3
"""Build unified HTML documentation site under docs/html/.

Sources:
  - docs/REFERENCE.md, USER_GUIDE.md, REMAINING_WORK.md, HANDOFF.md
  - configs/README.md
  - docs/html/_src/tables.md (field dictionary; smart HTML table layout)
  - docs/html/_src/project-overview.html (canonical overview body)
  - docs/html/_src/agentic-scaling-benchmark.html (canonical design report)

Usage:
  uv run python scripts/build_docs_html.py
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

import markdown
from markdown.extensions.fenced_code import FencedCodeExtension
from markdown.extensions.tables import TableExtension
from markdown.extensions.toc import TocExtension

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "html"
ASSETS = OUT / "assets"
SRC = OUT / "_src"

NAV = [
    ("index.html", "首页"),
    ("overview.html", "项目介绍"),
    ("manual.html", "用户手册"),
    ("reference.html", "参考资料"),
]

SITE_CSS = r"""
:root {
  --ink: #18212f;
  --muted: #667085;
  --line: #d6dee8;
  --navy: #18324d;
  --blue: #27628f;
  --pale-blue: #e4f0f8;
  --pale-green: #e5f4ed;
  --pale-yellow: #fff4d6;
  --pale-purple: #ece7f7;
  --pale-gray: #f5f7fa;
  --white: #ffffff;
  --good: #166534;
  --warn: #8a5a00;
  --bad: #b42318;
  --code-bg: #0f172a;
}
* { box-sizing: border-box; }
html { scroll-behavior: smooth; }
body {
  margin: 0;
  background: #f4f7fb;
  color: var(--ink);
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC",
    "Microsoft YaHei", Arial, sans-serif;
  line-height: 1.62;
}
.site-nav {
  position: sticky;
  top: 0;
  z-index: 40;
  background: rgba(24, 50, 77, 0.96);
  backdrop-filter: blur(8px);
  border-bottom: 1px solid rgba(255,255,255,0.08);
  box-shadow: 0 4px 16px rgba(24, 50, 77, 0.12);
}
.site-nav-inner {
  max-width: 1120px;
  margin: 0 auto;
  padding: 10px 20px;
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px 6px;
}
.site-nav .brand {
  color: #fff;
  font-weight: 700;
  font-size: 14px;
  margin-right: 10px;
  text-decoration: none;
  white-space: nowrap;
}
.site-nav a {
  color: #dce8f4;
  text-decoration: none;
  font-size: 13px;
  padding: 5px 10px;
  border-radius: 999px;
  border: 1px solid transparent;
}
.site-nav a:hover { background: rgba(255,255,255,0.1); color: #fff; }
.site-nav a.active {
  background: rgba(255,255,255,0.14);
  border-color: rgba(255,255,255,0.28);
  color: #fff;
  font-weight: 600;
}
.page { max-width: 1120px; margin: 0 auto; padding: 24px 28px 56px; }
.page.wide { max-width: 1180px; }
header.hero {
  background: linear-gradient(135deg, #18324d 0%, #244b70 55%, #2a5f8a 100%);
  color: var(--white);
  border-radius: 14px;
  padding: 34px 38px;
  box-shadow: 0 16px 40px rgba(24, 50, 77, 0.18);
}
header.hero h1 { margin: 0 0 12px; font-size: 28px; line-height: 1.25; }
header.hero p { margin: 0; color: #dce8f4; font-size: 15.5px; max-width: 940px; }
.meta { display: flex; flex-wrap: wrap; gap: 10px; margin-top: 18px; }
.pill {
  border: 1px solid rgba(255, 255, 255, 0.3);
  background: rgba(255, 255, 255, 0.1);
  border-radius: 999px;
  padding: 5px 11px;
  color: #eef6ff;
  font-size: 12.5px;
}
.md-body {
  margin-top: 22px;
  background: var(--white);
  border: 1px solid var(--line);
  border-radius: 12px;
  padding: 28px 30px;
  box-shadow: 0 8px 24px rgba(24, 50, 77, 0.06);
}
.md-body h1 {
  margin-top: 0; font-size: 26px; color: var(--navy);
  border-bottom: 2px solid var(--pale-blue); padding-bottom: 10px;
}
.md-body h2 {
  margin-top: 1.6em; font-size: 20px; color: var(--navy);
  border-bottom: 1px solid var(--line); padding-bottom: 6px;
}
.md-body h3 { margin-top: 1.25em; font-size: 16.5px; color: #23445f; }
.md-body h4 { margin-top: 1.1em; color: #34506a; }
.md-body p { margin: 0.7em 0; }
.md-body ul, .md-body ol { margin: 0.6em 0 0.6em 1.3em; }
.md-body li { margin: 0.3em 0; }
.md-body a { color: #175cd3; }
.md-body table {
  border-collapse: collapse; width: 100%; font-size: 13.5px; margin: 12px 0 16px;
}
.md-body th, .md-body td {
  border: 1px solid var(--line); padding: 8px 10px; vertical-align: top; text-align: left;
}
.md-body thead th { background: var(--blue); color: #fff; }
.md-body tbody th { background: var(--pale-gray); color: var(--navy); }
.md-body tr:hover td { background: #f8fafc; }
.md-body code {
  background: #eef2f6; color: #263445; border-radius: 5px; padding: 1px 5px;
  font-family: "SFMono-Regular", Consolas, monospace; font-size: 0.9em;
}
.md-body pre {
  background: var(--code-bg); color: #e5e7eb; border-radius: 10px;
  padding: 14px 16px; overflow-x: auto; font-size: 12.5px; line-height: 1.5;
}
.md-body pre code { background: transparent; color: inherit; padding: 0; }
.md-body blockquote {
  border-left: 4px solid var(--blue); background: #f1f7fc; margin: 14px 0;
  padding: 10px 14px; border-radius: 0 8px 8px 0; color: #344054;
}
.md-body hr { border: 0; border-top: 1px solid var(--line); margin: 24px 0; }
/* Smart field-dictionary tables (title/desc extracted from pseudo-header rows) */
.table-block {
  margin: 0 0 2rem;
  padding: 0 0 0.25rem;
  border-bottom: 1px solid var(--line);
}
.table-block:last-child { border-bottom: 0; }
.table-block .table-title {
  margin: 0 0 8px;
  font-size: 1.35rem;
  font-weight: 700;
  color: var(--navy);
  line-height: 1.35;
  border: 0;
  padding: 0;
}
.table-block .table-desc {
  margin: 0 0 14px;
  color: #475467;
  font-size: 14.5px;
  line-height: 1.55;
  max-width: 92ch;
}
.table-block .table-wrap {
  overflow-x: auto;
  border: 1px solid var(--line);
  border-radius: 10px;
  margin: 0 0 12px;
  background: #fff;
}
.table-block table {
  border-collapse: collapse;
  width: 100%;
  font-size: 13px;
  margin: 0;
  min-width: 520px;
}
.table-block th, .table-block td {
  border: 1px solid var(--line);
  padding: 8px 10px;
  vertical-align: top;
  text-align: left;
}
.table-block thead th {
  background: var(--blue);
  color: #fff;
  font-weight: 700;
  white-space: nowrap;
}
.table-block thead th.group {
  background: #1a4266;
  text-align: center;
  white-space: normal;
  font-size: 12.5px;
  letter-spacing: 0.02em;
}
.table-block tbody th {
  background: var(--pale-gray);
  color: var(--navy);
  font-weight: 700;
  white-space: nowrap;
}
.table-block tbody tr:hover td { background: #f8fafc; }
.table-block td.cat {
  background: #f3f6fa;
  font-weight: 600;
  color: var(--navy);
  white-space: nowrap;
  vertical-align: middle;
}
.toc { background: #f8fafc; border: 1px solid var(--line); border-radius: 10px; padding: 12px 16px; margin: 0 0 20px; }
.toc .toctitle { font-weight: 700; color: var(--navy); margin-bottom: 6px; display: block; }
.toc ul { margin: 0; padding-left: 1.2em; columns: 2; gap: 20px; }
.toc li { break-inside: avoid; font-size: 13.5px; }
footer.site-footer {
  color: var(--muted); font-size: 13px; margin-top: 28px; text-align: center; padding-bottom: 16px;
}
.card-grid {
  display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 14px; margin-top: 18px;
}
a.card {
  border: 1px solid var(--line); background: #fbfcfe; border-radius: 12px;
  padding: 16px 18px; text-decoration: none; color: inherit; display: block;
  transition: box-shadow .15s, border-color .15s;
}
a.card:hover { border-color: #9db8d0; box-shadow: 0 8px 20px rgba(24,50,77,.08); }
a.card h3 { margin: 0 0 8px; font-size: 16px; color: var(--navy); }
a.card p { margin: 0; font-size: 14px; color: #344054; }
a.card .tag {
  display: inline-block; margin-top: 10px; font-size: 12px; color: var(--blue);
  background: var(--pale-blue); border-radius: 999px; padding: 2px 8px;
}
@media (max-width: 860px) {
  .card-grid, .toc ul { grid-template-columns: 1fr; columns: 1; }
  header.hero { padding: 24px 18px; }
  header.hero h1 { font-size: 22px; }
  .md-body { padding: 18px 16px; }
  .page { padding: 16px 14px 40px; }
}
"""

OVERVIEW_LOCAL_CSS = r"""
nav.toc {
  margin-top: 22px; background: var(--white); border: 1px solid var(--line);
  border-radius: 12px; padding: 0; position: sticky; top: 56px; z-index: 5;
  box-shadow: 0 4px 16px rgba(24, 50, 77, 0.05); overflow: hidden;
}
.toc-head {
  display: flex; align-items: center; justify-content: space-between; gap: 12px;
  padding: 14px 18px; cursor: pointer; user-select: none; background: #f8fafc;
  border: 0; width: 100%; text-align: left; font: inherit; color: inherit;
}
.toc-head:hover { background: #f1f5f9; }
.toc-head h2 {
  margin: 0; font-size: 15px; color: var(--navy); text-transform: uppercase;
  letter-spacing: 0.04em; border: 0; padding: 0;
}
.toc-hint {
  font-size: 12px; color: var(--muted); font-weight: 400; margin-left: 8px;
  text-transform: none; letter-spacing: 0;
}
.toc-chevron {
  flex-shrink: 0; width: 28px; height: 28px; border-radius: 8px; border: 1px solid var(--line);
  background: var(--white); display: inline-flex; align-items: center; justify-content: center;
  color: var(--navy); font-size: 12px; transition: transform 0.18s ease;
}
nav.toc.is-collapsed .toc-chevron { transform: rotate(-90deg); }
.toc-body { padding: 4px 18px 16px; border-top: 1px solid var(--line); }
nav.toc.is-collapsed .toc-body { display: none; }
nav.toc ol { margin: 0; padding-left: 20px; columns: 2; gap: 28px; }
nav.toc li { margin: 5px 0; font-size: 14px; break-inside: avoid; }
nav.toc a { color: #175cd3; text-decoration: none; }
nav.toc a:hover { text-decoration: underline; }
main { margin-top: 22px; display: grid; gap: 20px; }
section {
  background: var(--white); border: 1px solid var(--line); border-radius: 12px;
  padding: 28px 30px; box-shadow: 0 8px 24px rgba(24, 50, 77, 0.06);
}
h2 {
  margin: 0 0 14px; font-size: 22px; color: var(--navy);
  padding-bottom: 10px; border-bottom: 2px solid var(--pale-blue);
}
h3 { margin: 22px 0 10px; color: #23445f; font-size: 17px; }
h4 { margin: 16px 0 8px; color: #34506a; font-size: 14.5px; }
.lead { font-size: 16.5px; color: #344054; }
.callout {
  border-left: 4px solid var(--blue); background: #f1f7fc; padding: 14px 16px;
  margin: 16px 0; border-radius: 0 8px 8px 0;
}
.callout.warn { border-left-color: #d97706; background: var(--pale-yellow); }
.callout.good { border-left-color: var(--good); background: var(--pale-green); }
.callout.purple { border-left-color: #6d28d9; background: var(--pale-purple); }
.grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 14px; }
.grid3 { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; }
.grid4 { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 10px; }
.card {
  border: 1px solid var(--line); background: #fbfcfe; border-radius: 10px; padding: 14px 16px;
}
.card h3 { margin: 0 0 8px; font-size: 15px; border: 0; padding: 0; }
.card p { margin: 0; font-size: 14px; color: #344054; }
.stat {
  text-align: center; border: 1px solid var(--line); border-radius: 10px; padding: 14px 10px;
  background: linear-gradient(180deg, #fbfcfe 0%, #f0f5fa 100%);
}
.stat .n { font-size: 22px; font-weight: 700; color: var(--navy); }
.stat .l { font-size: 12px; color: var(--muted); margin-top: 4px; }
.table-wrap {
  overflow-x: auto; margin: 14px 0 6px; border: 1px solid var(--line); border-radius: 10px;
}
table { border-collapse: collapse; width: 100%; font-size: 13.5px; min-width: 560px; }
th, td {
  border-bottom: 1px solid var(--line); border-right: 1px solid var(--line);
  padding: 9px 11px; vertical-align: top; text-align: left;
}
th:last-child, td:last-child { border-right: 0; }
tr:last-child td, tr:last-child th { border-bottom: 0; }
thead th { background: var(--blue); color: #fff; font-weight: 700; white-space: nowrap; }
tbody th { background: var(--pale-gray); color: var(--navy); font-weight: 700; white-space: nowrap; }
tbody tr:hover td { background: #f8fafc; }
code {
  background: #eef2f6; color: #263445; border-radius: 5px; padding: 1px 5px;
  font-family: "SFMono-Regular", Consolas, monospace; font-size: 0.9em;
}
pre {
  background: var(--code-bg); color: #e5e7eb; border-radius: 10px; padding: 16px 18px;
  overflow-x: auto; font-size: 12.5px; line-height: 1.5; margin: 12px 0;
}
pre code { background: transparent; color: inherit; padding: 0; font-size: inherit; }
.tag {
  display: inline-block; padding: 2px 8px; border-radius: 999px; font-size: 12px;
  border: 1px solid var(--line); background: var(--pale-gray); margin: 2px 4px 2px 0;
}
.tag.good { background: #e8f5ee; color: var(--good); border-color: #b7dfc8; }
.tag.warn { background: #fff5db; color: var(--warn); border-color: #f2d184; }
.tag.blue { background: var(--pale-blue); color: var(--blue); border-color: #b8d4ea; }
.flow {
  font-family: "SFMono-Regular", Consolas, monospace; font-size: 12.5px;
  background: var(--code-bg); color: #e2e8f0; border-radius: 10px; padding: 16px 18px;
  overflow-x: auto; white-space: pre; line-height: 1.5;
}
.steps { counter-reset: step; list-style: none; margin: 12px 0 0; padding: 0; }
.steps li {
  counter-increment: step; position: relative; padding: 12px 14px 12px 52px;
  border: 1px solid var(--line); border-radius: 10px; margin: 10px 0; background: #fbfcfe;
}
.steps li::before {
  content: counter(step); position: absolute; left: 14px; top: 12px; width: 26px; height: 26px;
  border-radius: 50%; background: var(--blue); color: #fff; font-size: 13px; font-weight: 700;
  display: flex; align-items: center; justify-content: center; line-height: 26px; text-align: center;
}
.doc-links a {
  display: inline-block; margin: 4px 8px 4px 0; padding: 8px 12px; border-radius: 8px;
  border: 1px solid var(--line); background: #f8fafc; color: #175cd3; text-decoration: none;
  font-size: 13.5px;
}
.doc-links a:hover { background: var(--pale-blue); }
header {
  background: linear-gradient(135deg, #18324d 0%, #244b70 55%, #2a5f8a 100%);
  color: var(--white); border-radius: 14px; padding: 36px 40px;
  box-shadow: 0 16px 40px rgba(24, 50, 77, 0.18);
}
header h1 { margin: 0 0 12px; font-size: 30px; }
header .subtitle { max-width: 940px; margin: 0; color: #dce8f4; font-size: 16.5px; }
@media (max-width: 860px) {
  .grid, .grid3, .grid4, nav.toc ol { grid-template-columns: 1fr; columns: 1; }
}
"""

DESIGN_LOCAL_CSS = r"""
main { margin-top: 22px; display: grid; gap: 20px; }
section {
  background: var(--white); border: 1px solid var(--line); border-radius: 12px;
  padding: 26px 28px; box-shadow: 0 8px 24px rgba(24, 50, 77, 0.06);
}
h2 {
  margin: 0 0 16px; font-size: 22px; color: var(--navy);
  border-bottom: 2px solid var(--pale-blue); padding-bottom: 8px;
}
h3 { margin: 22px 0 10px; color: #23445f; font-size: 17px; }
/* --- 图示与结构化排版 --- */
figure.diagram {
  margin: 20px 0; padding: 18px; background: var(--white);
  border: 1px solid var(--line); border-radius: 12px;
}
figure.diagram svg { display: block; width: 100%; height: auto; max-width: 900px; margin: 0 auto; }
figure.diagram figcaption {
  margin-top: 12px; color: var(--muted); font-size: 13.5px; text-align: center;
}
.callout {
  margin: 16px 0; padding: 14px 16px; border-radius: 10px;
  border-left: 4px solid var(--blue); background: var(--pale-blue);
}
.callout.warn { border-left-color: var(--warn); background: var(--pale-yellow); }
.callout.good { border-left-color: var(--good); background: var(--pale-green); }
.callout p:first-child { margin-top: 0; }
.callout p:last-child { margin-bottom: 0; }
.step {
  margin: 18px 0; padding: 16px 18px; background: var(--white);
  border: 1px solid var(--line); border-radius: 12px;
}
.step > h3 { margin-top: 0; display: flex; align-items: center; gap: 10px; }
.step > h3 .num {
  display: inline-flex; align-items: center; justify-content: center;
  width: 26px; height: 26px; border-radius: 50%;
  background: var(--blue); color: #fff; font-size: 14px; flex: 0 0 auto;
}
.kv { display: grid; grid-template-columns: max-content 1fr; gap: 6px 16px; margin: 10px 0; }
.kv dt { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; color: var(--navy); font-weight: 600; }
.kv dd { margin: 0; color: var(--ink); }
.two-col { display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 16px; }

.table-wrap { overflow-x: auto; margin: 12px 0; border: 1px solid var(--line); border-radius: 10px; }
table { border-collapse: collapse; width: 100%; font-size: 13px; }
th, td { border: 1px solid var(--line); padding: 8px 10px; vertical-align: top; text-align: left; }
thead th { background: var(--blue); color: #fff; }
tbody th { background: var(--pale-gray); color: var(--navy); }
code { background: #eef2f6; border-radius: 4px; padding: 1px 5px; font-size: 0.9em; }
.callout {
  border-left: 4px solid var(--blue); background: #f1f7fc; padding: 12px 14px;
  margin: 14px 0; border-radius: 0 8px 8px 0;
}
header {
  background: linear-gradient(135deg, #18324d 0%, #244b70 100%); color: #fff;
  border-radius: 14px; padding: 34px 38px; box-shadow: 0 16px 40px rgba(24,50,77,.18);
}
header h1 { margin: 0 0 12px; font-size: 28px; }
header p { margin: 0; color: #dce8f4; }
.meta { display: flex; flex-wrap: wrap; gap: 10px; margin-top: 18px; }
.pill {
  border: 1px solid rgba(255,255,255,.3); background: rgba(255,255,255,.1);
  border-radius: 999px; padding: 5px 11px; color: #eef6ff; font-size: 12.5px;
}
footer { color: var(--muted); font-size: 13px; margin-top: 22px; text-align: center; }
"""


def nav_html(active: str) -> str:
    links = []
    for href, label in NAV:
        cls = ' class="active"' if href == active else ""
        links.append(f'<a href="{href}"{cls}>{label}</a>')
    return (
        '<nav class="site-nav" aria-label="文档导航">'
        '<div class="site-nav-inner">'
        '<a class="brand" href="index.html">aibench · docs/html</a>'
        + "".join(links)
        + "</div></nav>"
    )


def shell(title: str, active: str, body: str, *, wide: bool = False) -> str:
    page_cls = "page wide" if wide else "page"
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{title}</title>
  <link rel="stylesheet" href="assets/site.css" />
</head>
<body>
{nav_html(active)}
<div class="{page_cls}">
{body}
<footer class="site-footer">
  AI-Coding-Assist Benchmark · 文档站点 docs/html · 本地打开即可浏览
</footer>
</div>
</body>
</html>
"""


def rewrite_md_links(text: str) -> str:
    pairs = [
        # docs/*.md paths like ](html/reference.html) → sibling
        (r"\]\(html/([A-Za-z0-9_.-]+\.html)(#[^)]*)?\)", r"](\1\2)"),
        (r"\]\(\.\./docs/html/([A-Za-z0-9_.-]+\.html)(#[^)]*)?\)", r"](\1\2)"),
        (r"\]\(docs/html/([A-Za-z0-9_.-]+\.html)(#[^)]*)?\)", r"](\1\2)"),
        (r"\]\(\.\./aibench-project-overview\.html(#[^)]*)?\)", r"](project-overview.html\1)"),
        (r"\]\(aibench-project-overview\.html(#[^)]*)?\)", r"](project-overview.html\1)"),
        (
            r"\]\(\.\./agentic-scaling-benchmark\.html(#[^)]*)?\)",
            r"](agentic-scaling-benchmark.html\1)",
        ),
        (r"\]\(agentic-scaling-benchmark\.html(#[^)]*)?\)", r"](agentic-scaling-benchmark.html\1)"),
        (r"\]\(\.\./agentic_scaling_benchmark_tables\.md(#[^)]*)?\)", r"](tables.html\1)"),
        (r"\]\(agentic_scaling_benchmark_tables\.md(#[^)]*)?\)", r"](tables.html\1)"),
        (r"\]\(REFERENCE\.md(#[^)]*)?\)", r"](reference.html\1)"),
        (r"\]\(\.\./docs/REFERENCE\.md(#[^)]*)?\)", r"](reference.html\1)"),
        (r"\]\(docs/REFERENCE\.md(#[^)]*)?\)", r"](reference.html\1)"),
        (r"\]\(USER_GUIDE\.md(#[^)]*)?\)", r"](user-guide.html\1)"),
        (r"\]\(\.\./docs/USER_GUIDE\.md(#[^)]*)?\)", r"](user-guide.html\1)"),
        (r"\]\(REMAINING_WORK\.md(#[^)]*)?\)", r"](remaining-work.html\1)"),
        (r"\]\(\.\./configs/README\.md(#[^)]*)?\)", r"](configs.html\1)"),
        (r"\]\(configs/README\.md(#[^)]*)?\)", r"](configs.html\1)"),
    ]
    for pat, rep in pairs:
        text = re.sub(pat, rep, text)
    return text


def md_to_html(text: str) -> str:
    text = rewrite_md_links(text)
    return markdown.markdown(
        text,
        extensions=[
            TableExtension(),
            FencedCodeExtension(),
            TocExtension(permalink=False, toc_depth="2-3"),
            "sane_lists",
        ],
        output_format="html5",
    )


def write_md_page(src: Path, out_name: str, title: str, active: str, hero_sub: str) -> None:
    raw = src.read_text(encoding="utf-8")
    # strip leading "展示版" banner lines from source if present (avoid double notice)
    raw = re.sub(
        r"^> \*\*展示版\*\*[^\n]*\n+(?:>[^\n]*\n+)*",
        "",
        raw,
        count=1,
        flags=re.M,
    )
    html_body = md_to_html(raw)
    hero = f"""
<header class="hero">
  <h1>{title}</h1>
  <p>{hero_sub}</p>
  <div class="meta">
    <span class="pill">HTML 归档 · docs/html</span>
    <span class="pill">源文件 · {src.relative_to(ROOT)}</span>
  </div>
</header>
"""
    body = f'{hero}<article class="md-body">\n{html_body}\n</article>'
    (OUT / out_name).write_text(shell(title, active, body), encoding="utf-8")
    print(f"wrote {out_name}")


# ---------------------------------------------------------------------------
# Field-dictionary tables: title/description rows + multi-level headers
# ---------------------------------------------------------------------------


def _split_md_row(line: str) -> list[str]:
    line = line.rstrip("\n")
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|"):
        line = line[:-1]
    return [c.strip() for c in line.split("|")]


def _is_sep_row(cells: list[str]) -> bool:
    if not cells:
        return False
    return all(re.fullmatch(r":?-{3,}:?", c or "") is not None for c in cells)


def _nonempty_count(cells: list[str]) -> int:
    return sum(1 for c in cells if c)


def _is_blank_row(cells: list[str]) -> bool:
    return _nonempty_count(cells) == 0


def _is_title_or_desc_row(cells: list[str]) -> bool:
    """True when only the first cell has text (Markdown pseudo title/desc row)."""
    if not cells:
        return False
    if not cells[0]:
        return False
    return all(not c for c in cells[1:])


def _escape(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def _parse_md_tables(text: str) -> list[dict]:
    """Parse pipe tables; return list of {title, description, header_rows, body_rows}."""
    lines = text.splitlines()
    tables: list[dict] = []
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        if not line.strip().startswith("|"):
            i += 1
            continue
        block: list[list[str]] = []
        while i < n and lines[i].strip().startswith("|"):
            cells = _split_md_row(lines[i])
            if not _is_sep_row(cells):
                block.append(cells)
            i += 1
        if not block:
            continue
        # normalize width
        width = max(len(r) for r in block)
        block = [(r + [""] * width)[:width] for r in block]

        # Drop leading all-empty row (dummy markdown thead)
        while block and _is_blank_row(block[0]):
            block.pop(0)

        title = ""
        description = ""
        if block and _is_title_or_desc_row(block[0]):
            title = block.pop(0)[0]
        if block and _is_title_or_desc_row(block[0]):
            # Prefer longer text as description if second meta row
            description = block.pop(0)[0]
        while block and _is_blank_row(block[0]):
            block.pop(0)

        if not block:
            continue

        # Detect multi-level header: sparse group row + dense detail row
        header_rows: list[list[str]] = []
        body_start = 0
        if len(block) >= 2:
            r0, r1 = block[0], block[1]
            n0, n1 = _nonempty_count(r0), _nonempty_count(r1)
            # group header: fewer non-empty than detail header; detail has most columns filled
            if n0 >= 2 and n1 >= 3 and n0 < n1 and n1 >= max(3, width // 3):
                header_rows = [r0, r1]
                body_start = 2
            else:
                header_rows = [r0]
                body_start = 1
        else:
            header_rows = [block[0]]
            body_start = 1

        body_rows = block[body_start:]
        # If we mis-detected and body looks empty but header was data-like, keep simple
        tables.append(
            {
                "title": title,
                "description": description,
                "header_rows": header_rows,
                "body_rows": body_rows,
                "width": width,
            }
        )
    return tables


def _colspan_cells(row: list[str], width: int) -> list[tuple[str, int]]:
    """Merge trailing empty cells into colspan (for group headers)."""
    row = (row + [""] * width)[:width]
    out: list[tuple[str, int]] = []
    i = 0
    while i < width:
        text = row[i]
        span = 1
        while i + span < width and not row[i + span]:
            span += 1
        out.append((text, span))
        i += span
    return out


def _render_thead(header_rows: list[list[str]], width: int) -> str:
    parts = ["<thead>"]
    if len(header_rows) == 2:
        group, detail = header_rows
        parts.append("<tr>")
        for text, span in _colspan_cells(group, width):
            label = _escape(text) if text else "&nbsp;"
            parts.append(f'<th class="group" colspan="{span}">{label}</th>')
        parts.append("</tr>")
        parts.append("<tr>")
        for c in (detail + [""] * width)[:width]:
            parts.append(f"<th>{_escape(c)}</th>")
        parts.append("</tr>")
    else:
        parts.append("<tr>")
        for c in (header_rows[0] + [""] * width)[:width]:
            parts.append(f"<th>{_escape(c)}</th>")
        parts.append("</tr>")
    parts.append("</thead>")
    return "\n".join(parts)


def _apply_first_col_rowspan(body_rows: list[list[str]], width: int) -> list[tuple[list[str], int]]:
    """Return list of (row_cells, rowspan_for_first_col). rowspan=0 means skip first cell."""
    rows = [(r + [""] * width)[:width] for r in body_rows]
    result: list[tuple[list[str], int]] = []
    i = 0
    while i < len(rows):
        key = rows[i][0]
        span = 1
        if key:
            while i + span < len(rows) and rows[i + span][0] == key:
                span += 1
        result.append((rows[i], span))
        for k in range(1, span):
            result.append((rows[i + k], 0))
        i += span
    return result


def _looks_like_field_dict(header_rows: list[list[str]]) -> bool:
    if not header_rows:
        return False
    h = header_rows[-1]
    joined = " ".join(h)
    return "一级列" in joined and "细分列" in joined


def _render_table_block(t: dict, index: int) -> str:
    width = t["width"]
    title = t["title"] or f"表 {index + 1}"
    desc = t["description"]
    header_rows = t["header_rows"]
    body_rows = t["body_rows"]
    slug = re.sub(r"[^\w\u4e00-\u9fff]+", "-", title).strip("-") or f"table-{index}"

    thead = _render_thead(header_rows, width)
    tbody_parts = ["<tbody>"]
    if _looks_like_field_dict(header_rows):
        for cells, rowspan in _apply_first_col_rowspan(body_rows, width):
            tbody_parts.append("<tr>")
            if rowspan > 0:
                tbody_parts.append(f'<td class="cat" rowspan="{rowspan}">{_escape(cells[0])}</td>')
            for c in cells[1:]:
                tbody_parts.append(f"<td>{_escape(c)}</td>")
            tbody_parts.append("</tr>")
    else:
        for cells in body_rows:
            cells = (cells + [""] * width)[:width]
            tbody_parts.append("<tr>")
            for c in cells:
                tbody_parts.append(f"<td>{_escape(c)}</td>")
            tbody_parts.append("</tr>")
    tbody_parts.append("</tbody>")
    tbody = "\n".join(tbody_parts)

    desc_html = f'<p class="table-desc">{_escape(desc)}</p>' if desc else ""
    return (
        f'<section class="table-block" id="{_escape(slug)}">\n'
        f'  <h2 class="table-title">{_escape(title)}</h2>\n'
        f"  {desc_html}\n"
        f'  <div class="table-wrap">\n'
        f"    <table>\n"
        f"{thead}\n"
        f"{tbody}\n"
        f"    </table>\n"
        f"  </div>\n"
        f"</section>"
    )


def write_tables_page() -> None:
    """Build tables.html with smart extraction of title/description pseudo-rows."""
    candidates = [
        SRC / "tables.md",
        ROOT / "agentic_scaling_benchmark_tables.md",
    ]
    src = next((p for p in candidates if p.is_file()), None)
    if src is None:
        print("skip tables.html: no source")
        return

    raw = src.read_text(encoding="utf-8")
    raw = re.sub(
        r"^> \*\*展示版\*\*[^\n]*\n+(?:>[^\n]*\n+)*",
        "",
        raw,
        count=1,
        flags=re.M,
    )
    # Drop markdown section headings; titles come from table meta rows
    raw = re.sub(r"^#+\s+.*$", "", raw, flags=re.M)

    tables = _parse_md_tables(raw)
    blocks = [_render_table_block(t, i) for i, t in enumerate(tables)]
    intro = """
<p class="lead" style="margin-top:0">
  下列三张表对应 Agentic Scaling 结果表口径：
  <strong>项目效果综述表</strong>、<strong>通用结果总表</strong> 与 <strong>字段说明</strong>。
  表名与说明已从原 Markdown「伪表头行」提取；通用总表支持一级/二级表头合并展示。
</p>
"""
    hero = f"""
<header class="hero">
  <h1>结果表字段字典</h1>
  <p>项目效果综述表、通用结果总表样例与字段说明（Agentic Scaling 列口径）。</p>
  <div class="meta">
    <span class="pill">HTML 归档 · docs/html</span>
    <span class="pill">源文件 · {src.relative_to(ROOT)}</span>
  </div>
</header>
"""
    body = f'{hero}<article class="md-body">\n{intro}\n' + "\n\n".join(blocks) + "\n</article>"
    (OUT / "tables.html").write_text(
        shell("结果表字段字典", "tables.html", body, wide=True),
        encoding="utf-8",
    )
    print(f"wrote tables.html ({len(tables)} tables) from {src.relative_to(ROOT)}")


def ensure_src_copies() -> None:
    """Ensure editable HTML sources exist under docs/html/_src/."""
    SRC.mkdir(parents=True, exist_ok=True)
    for name in ("project-overview.html", "agentic-scaling-benchmark.html"):
        dest = SRC / name
        if dest.is_file():
            continue
        built = OUT / name
        if built.is_file() and built.stat().st_size > 1000:
            shutil.copy2(built, dest)
            print(f"seeded _src/{name} from built output")
        else:
            print(f"warning: missing docs/html/_src/{name}")


def adapt_standalone_html(
    src_path: Path,
    out_name: str,
    active: str,
    local_css: str,
    *,
    wide: bool = False,
) -> None:
    text = src_path.read_text(encoding="utf-8")
    # Drop existing site-nav if re-building from previous output
    text = re.sub(
        r'<nav class="site-nav"[^>]*>.*?</nav>\s*',
        "",
        text,
        count=1,
        flags=re.S,
    )
    # Replace head styles with shared css + page local
    text = re.sub(
        r"<style\b[^>]*>.*?</style>",
        "",
        text,
        flags=re.S,
    )
    text = re.sub(
        r'<link rel="stylesheet" href="assets/site\.css"\s*/?>\s*',
        "",
        text,
    )
    inject = (
        f'<link rel="stylesheet" href="assets/site.css" />\n'
        f'    <style id="page-local">\n{local_css}\n    </style>\n'
    )
    text = text.replace("</head>", inject + "</head>", 1)
    if "<body>" in text:
        text = text.replace("<body>", "<body>\n" + nav_html(active) + "\n", 1)
    elif "<body " in text:
        text = re.sub(r"<body([^>]*)>", r"<body\1>\n" + nav_html(active) + "\n", text, count=1)

    # Normalize internal doc links
    replacements = {
        'href="docs/REFERENCE.md"': 'href="reference.html"',
        'href="../docs/REFERENCE.md"': 'href="reference.html"',
        'href="docs/USER_GUIDE.md"': 'href="user-guide.html"',
        'href="../docs/USER_GUIDE.md"': 'href="user-guide.html"',
        'href="configs/README.md"': 'href="configs.html"',
        'href="../configs/README.md"': 'href="configs.html"',
        'href="agentic_scaling_benchmark_tables.md"': 'href="tables.html"',
        'href="../agentic_scaling_benchmark_tables.md"': 'href="tables.html"',
        'href="docs/REMAINING_WORK.md"': 'href="remaining-work.html"',
        'href="aibench-project-overview.html"': 'href="project-overview.html"',
        'href="../aibench-project-overview.html"': 'href="project-overview.html"',
    }
    for a, b in replacements.items():
        text = text.replace(a, b)

    text = text.replace("docs/REFERENCE.md", "reference.html")
    text = text.replace("docs/USER_GUIDE.md", "user-guide.html")
    text = text.replace("agentic_scaling_benchmark_tables.md", "tables.html")
    text = text.replace("aibench-project-overview.html", "project-overview.html")
    text = text.replace(
        "/Users/lishanni/code/agent-study/outputs/agentic_scaling_benchmark_tables/agentic_scaling_benchmark_tables.xlsx",
        "见本站 tables.html（字段字典）",
    )

    # Ensure page wrapper for design report
    if wide and 'class="page"' not in text and 'class="page wide"' not in text:
        text = text.replace(
            nav_html(active) + "\n",
            nav_html(active) + '\n<div class="page wide">\n',
            1,
        )
        if "</body>" in text and text.count('<div class="page') > text.count("</div>"):
            text = text.replace("</body>", "</div>\n</body>", 1)

    (OUT / out_name).write_text(text, encoding="utf-8")
    print(f"wrote {out_name}")


def write_index() -> None:
    cards = [
        (
            "project-overview.html",
            "项目介绍演示",
            "目标、架构、操作规程、CLI 参数、科学效度、产物与设计表映射",
        ),
        (
            "reference.html",
            "参考手册 REFERENCE",
            "参数级权威参考：CLI / 配置 / Schema / 门禁 / 映射 / FAQ",
        ),
        ("user-guide.html", "用户向导 USER_GUIDE", "环境准备、快速上手、日常操作速查"),
        (
            "agentic-scaling-benchmark.html",
            "结果表设计报告",
            "统一粒度、综述表、通用总表、落盘与落地阶段",
        ),
        ("tables.html", "结果表字段字典", "综述表样例、通用总表列、字段说明"),
        ("configs.html", "生产配置说明", "configs/ 下 Agent / 模型 / Run / 消融矩阵"),
        ("remaining-work.html", "未尽事项", "已完成能力与可选增强"),
    ]
    card_html = (
        '<div class="card-grid">'
        + "".join(
            f'<a class="card" href="{h}"><h3>{t}</h3><p>{d}</p><span class="tag">打开 →</span></a>'
            for h, t, d in cards
        )
        + "</div>"
    )
    body = f"""
<header class="hero">
  <h1>AI-Coding-Assist Benchmark · 文档站</h1>
  <p>本目录归档与项目介绍演示相关的全部 HTML 页面：介绍、参考手册、用户向导、结果表设计与字段字典、生产配置与未尽事项。统一样式，顶部导航可互相跳转。</p>
  <div class="meta">
    <span class="pill">docs/html</span>
    <span class="pill">Benchmark · AI-Coding-Assist</span>
    <span class="pill">展示形态 · HTML</span>
  </div>
</header>
<section class="md-body" style="margin-top:22px">
  <h2 style="margin-top:0">页面索引</h2>
  <p>推荐从 <a href="project-overview.html"><strong>项目介绍演示</strong></a> 开始，参数细节查 <a href="reference.html">参考手册</a>。</p>
  {card_html}
  <h2>与源文件对应</h2>
  <table>
    <thead><tr><th>HTML 页面</th><th>源文件</th></tr></thead>
    <tbody>
      <tr><td><a href="project-overview.html">project-overview.html</a></td><td>docs/html/_src/project-overview.html</td></tr>
      <tr><td><a href="handoff.html">handoff.html</a></td><td>docs/HANDOFF.md</td></tr>
      <tr><td><a href="reference.html">reference.html</a></td><td>docs/REFERENCE.md</td></tr>
      <tr><td><a href="user-guide.html">user-guide.html</a></td><td>docs/USER_GUIDE.md</td></tr>
      <tr><td><a href="agentic-scaling-benchmark.html">agentic-scaling-benchmark.html</a></td><td>docs/html/_src/agentic-scaling-benchmark.html</td></tr>
      <tr><td><a href="tables.html">tables.html</a></td><td>docs/html/_src/tables.md</td></tr>
      <tr><td><a href="configs.html">configs.html</a></td><td>configs/README.md</td></tr>
      <tr><td><a href="remaining-work.html">remaining-work.html</a></td><td>docs/REMAINING_WORK.md</td></tr>
    </tbody>
  </table>
  <p>重建本站：<code>uv run python scripts/build_docs_html.py</code></p>
</section>
"""
    (OUT / "index.html").write_text(
        shell("aibench 文档站 · docs/html", "index.html", body),
        encoding="utf-8",
    )
    print("wrote index.html")


def write_body_page(src_name: str, out_name: str, title: str) -> None:
    """Wrap a hand-written body fragment from _src/ in the shared shell."""
    body = (SRC / src_name).read_text(encoding="utf-8")
    (OUT / out_name).write_text(shell(title, out_name, body), encoding="utf-8")


INDEX_BODY = """
<header class="hero">
  <h1>AI-Coding-Assist Benchmark</h1>
  <p>从现网真实会话记录生成具备能力区分度的编程 benchmark 用例，并给出可复现的度量流程。</p>
  <div class="meta">
    <span class="pill">数据来源 · 现网 trace</span>
    <span class="pill">度量 · 三锚点校准</span>
    <span class="pill">当前集合 · 31 条</span>
  </div>
</header>
<section class="md-body" style="margin-top:22px">
  <h2 style="margin-top:0">文档导航</h2>
  <div class="card-grid">
    <a class="card" href="overview.html"><h3>项目介绍</h3>
      <p>背景与目标、流水线架构、反向构造原理、关键模块技术细节、实测难度分布与用例样例</p>
      <span class="tag">打开 →</span></a>
    <a class="card" href="manual.html"><h3>用户手册</h3>
      <p>环境准备、六步端到端流程、每个参数的含义与实测效果、难度调整、对外分发、故障排查</p>
      <span class="tag">打开 →</span></a>
    <a class="card" href="reference.html"><h3>参考资料</h3>
      <p>方案设计依据、用例与校准结果的数据格式、效度门禁规则、已发布数据、术语表</p>
      <span class="tag">打开 →</span></a>
  </div>

  <h2>一分钟了解</h2>
  <p>让大模型直接编题，会稳定产出送分题——基线集合中 <strong>75.2%</strong> 的用例所有配置都能解出。
     三次独立干预实验证明，无论输入什么，生成器都输出同一种难度。</p>
  <p>本项目改为<strong>反向构造</strong>：从真实会话中重放出文件被修改前与修改后的两个版本，
     以前者为待修复代码、后者为参考解，模型只负责编写能区分两版的测试。
     缺陷来自真人真错，模型无法把任务改简单。</p>
  <div class="table-wrap"><table>
    <thead><tr><th>指标</th><th>模型编题 <code>auto-v0</code></th><th>反向构造 <code>reverse-v1</code></th></tr></thead>
    <tbody>
      <tr><td>易 / 中 / 难</td><td>75.2 / 16.2 / 8.6</td><td><strong>22.6 / 74.2 / 3.2</strong></td></tr>
      <tr><td>平均通过率</td><td>0.677</td><td><strong>0.527</strong></td></tr>
      <tr><td>有区分度占比</td><td>22.2%</td><td><strong>83.9%</strong></td></tr>
    </tbody>
  </table></div>
  <p>中档 74.2%（95% 区间 [56.8%, 86.3%]）已达成 70% 的目标。
     完整数据、置信区间与已知边界见<a href="overview.html#distribution">项目介绍</a>。</p>

  <h2>待讨论</h2>
  <div class="callout warn">
    <p><strong>语言覆盖是当前产量的首要上限。</strong> 3,312 条草稿中可重建的文件版本里，
       <strong>4,472 个因语言未注册被丢弃</strong>，而最终可用的仅 194 个——丢弃量约为可用量的 23 倍。
       C++ 与 Rust 需要编译，而评分工作区只有两个文件。</p>
    <p>问题陈述、实测数据、技术阻塞点、四个可选方案及其代价与风险，
       见<a href="overview.html#open-lang">项目介绍 · 第 8 节</a>。</p>
  </div>

  <h2>重建本站</h2>
  <p><code>uv run python scripts/build_docs_html.py</code>　—　内容源文件位于 <code>docs/html/_src/</code>。</p>
</section>
"""


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    ASSETS.mkdir(parents=True, exist_ok=True)
    (ASSETS / "site.css").write_text(SITE_CSS, encoding="utf-8")

    (OUT / "index.html").write_text(
        shell("AI-Coding-Assist Benchmark · 文档站", "index.html", INDEX_BODY),
        encoding="utf-8",
    )
    write_body_page("overview.html", "overview.html", "项目介绍 · AI-Coding-Assist Benchmark")
    write_body_page("manual.html", "manual.html", "用户手册 · AI-Coding-Assist Benchmark")
    write_body_page("reference.html", "reference.html", "参考资料 · AI-Coding-Assist Benchmark")

    for stale in (
        "project-overview.html",
        "user-guide.html",
        "agentic-scaling-benchmark.html",
        "tables.html",
        "configs.html",
        "remaining-work.html",
        "handoff.html",
    ):
        (OUT / stale).unlink(missing_ok=True)

    print(f"built 4 pages -> {OUT}")


if __name__ == "__main__":
    main()
