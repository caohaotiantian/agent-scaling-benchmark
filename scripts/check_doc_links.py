#!/usr/bin/env python3
"""Fail when a document links somewhere that is not there, or labels a link with a dead name.

The documentation site is built from three hand-written fragments and `build_docs_html.py`
deletes the seven pages an earlier layout produced. README linked six of those seven, so the
rebuild command the README recommends removed the pages the README pointed at — 25 dead links
across five files, and the deadest of them was the one labelled "接手工作请先读这一页".

Nothing noticed, because no gate looked. This is that gate. It checks three things, because
fixing only the first is how a link ends up green while still misleading its reader:

* the target file exists — Markdown and HTML alike, since the site is now HTML-only;
* the ``#fragment`` resolves to an ``id`` in the target, when the target is HTML;
* the visible label does not name a page that no longer exists. Repointing 25 hrefs while
  leaving their labels saying ``tables.html`` produces a green gate and a reader who clicks
  "字段字典" and lands on the glossary.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

#: Directories with no reader-facing documents, or whose contents are not checked in.
#:
#: ``_src`` holds the site's input fragments. Their links reach the reader through the built
#: pages, which *are* checked, so scanning both would only double-report — and the directory
#: also holds two orphans (``project-overview.html``, ``agentic-scaling-benchmark.html``) that
#: no longer feed any page and whose links point at the layout they were written for.
SKIP_DIRS = {
    ".git",
    ".venv",
    "node_modules",
    "runs",
    ".pytest_cache",
    ".ruff_cache",
    ".agent",
    "_src",
}

#: Pages `build_docs_html.py` deletes on every run. A label naming one of these is stale even
#: when its href was repointed at a page that does exist.
DELETED_PAGES = {
    "project-overview.html",
    "user-guide.html",
    "agentic-scaling-benchmark.html",
    "tables.html",
    "configs.html",
    "remaining-work.html",
    "handoff.html",
}

MD_LINK = re.compile(r"\[([^\]]*)\]\(([^)\s]+)\)")
HTML_LINK = re.compile(r"""<a\b[^>]*\bhref=["']([^"']+)["'][^>]*>(.*?)</a>""", re.S | re.I)
HTML_ID = re.compile(r"""\bid=["']([^"']+)["']""")

#: A run directory cited inside a code span: `runs/ablation_20260814_111227`. These resolve to
#: nothing in a clone — `/runs/` is gitignored and `git ls-files runs` returns 0 — and the link
#: checker above cannot see them, because they are code spans rather than links and `runs` is in
#: SKIP_DIRS. Sixteen such citations sat in tracked documents, each reading like a path the
#: reader could open.
RUN_CITATION = re.compile(r"`(runs/[A-Za-z0-9_.\-]*_\d{8}_\d{6}[A-Za-z0-9_.\-/]*)`")

#: What a document must say once for its run citations to be honest. Checked per document rather
#: than per citation: the reader needs the convention stated where they are reading, and
#: annotating all sixteen inline would be unreadable.
LOCAL_ARTIFACT_NOTE = "本地产物，未随仓库分发"


def ids_of(path: Path) -> set[str]:
    if path.suffix.lower() not in {".html", ".htm"}:
        return set()
    try:
        return set(HTML_ID.findall(path.read_text(encoding="utf-8")))
    except OSError:
        return set()


def check(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    pairs = MD_LINK.findall(text)
    if path.suffix.lower() in {".html", ".htm"}:
        pairs += [(label, href) for href, label in HTML_LINK.findall(text)]

    problems: list[str] = []
    for label, target in pairs:
        href, _, fragment = target.partition("#")
        href = href.strip()
        plain = re.sub(r"<[^>]+>", "", label).strip()

        if href and not href.startswith(("http://", "https://", "mailto:", "<")):
            dest = (path.parent / href).resolve()
            if not dest.exists():
                problems.append(f"[{plain}] -> {target}  (no such file)")
                continue
            if fragment and dest.suffix.lower() in {".html", ".htm"}:
                known = ids_of(dest)
                if known and fragment not in known:
                    problems.append(f"[{plain}] -> {target}  (no id={fragment!r})")

        stale = sorted(p for p in DELETED_PAGES if p in plain)
        if stale:
            problems.append(f"[{plain}] -> {target}  (label names deleted page {stale[0]})")

    cited_runs = sorted(set(RUN_CITATION.findall(text)))
    if cited_runs and LOCAL_ARTIFACT_NOTE not in text:
        shown = ", ".join(cited_runs[:3]) + ("…" if len(cited_runs) > 3 else "")
        problems.append(
            f"cites {len(cited_runs)} run director{'y' if len(cited_runs) == 1 else 'ies'} "
            f"({shown}) but never says they are local: `/runs/` is gitignored, so none of these "
            f"exists in a clone. Add the note “{LOCAL_ARTIFACT_NOTE}” to this document."
        )
    return problems


def main() -> int:
    docs = sorted(
        p
        for p in ROOT.rglob("*")
        if p.suffix.lower() in {".md", ".html", ".htm"}
        and not any(part in SKIP_DIRS for part in p.relative_to(ROOT).parts)
    )
    total = 0
    for path in docs:
        found = check(path)
        if not found:
            continue
        total += len(found)
        print(f"{path.relative_to(ROOT)}")
        for line in found:
            print(f"    {line}")
    print(f"checked {len(docs)} document(s): {total} problem(s)")
    return 1 if total else 0


if __name__ == "__main__":
    sys.exit(main())
