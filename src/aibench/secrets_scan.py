"""Scan case JSON / text for likely secrets before publish or after generate."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from aibench.io_util import load_json

_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("openai_sk", re.compile(r"sk-[A-Za-z0-9]{20,}")),
    ("aws_key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("private_key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("password_assign", re.compile(r"(?i)(password|passwd|pwd)\s*[:=]\s*\S+")),
    ("api_key_assign", re.compile(r"(?i)(api[_-]?key|secret|token)\s*[:=]\s*['\"]?[^\s'\"]{8,}")),
    ("bearer", re.compile(r"(?i)bearer\s+[A-Za-z0-9\-._~+/]+=*")),
]


@dataclass
class Finding:
    path: str
    rule: str
    snippet: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def scan_text(text: str, *, path: str = "<text>") -> list[Finding]:
    out: list[Finding] = []
    for name, pat in _PATTERNS:
        for m in pat.finditer(text or ""):
            snip = m.group(0)
            if len(snip) > 80:
                snip = snip[:40] + "..." + snip[-20:]
            out.append(Finding(path=path, rule=name, snippet=snip))
    return out


def scan_case_dict(case: dict[str, Any], *, path: str = "case") -> list[Finding]:
    findings: list[Finding] = []
    findings.extend(scan_text(str(case.get("prompt") or ""), path=f"{path}:prompt"))
    for f in (case.get("context") or {}).get("files") or []:
        p = f.get("path") or "file"
        findings.extend(scan_text(str(f.get("content") or ""), path=f"{path}:{p}"))
    for g in (case.get("grader") or {}).get("gold_files") or []:
        p = g.get("path") or "gold"
        findings.extend(scan_text(str(g.get("content") or ""), path=f"{path}:gold:{p}"))
    return findings


def scan_case_file(path: Path) -> list[Finding]:
    return scan_case_dict(load_json(path), path=str(path.name))


def scan_case_dir(directory: Path) -> dict[str, Any]:
    from aibench.cases import is_case_json_path

    all_f: list[dict[str, Any]] = []
    files = 0
    for p in sorted(directory.glob("*.json")):
        if not is_case_json_path(p):
            continue
        files += 1
        for f in scan_case_file(p):
            all_f.append(f.to_dict())
    return {
        "directory": str(directory),
        "files_scanned": files,
        "finding_count": len(all_f),
        "clean": len(all_f) == 0,
        "findings": all_f,
    }
