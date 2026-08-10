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
    # The optional quote after the name matches a quoted key, as in {"token": "..."} — the
    # exact shape `redact_source` may decline to rewrite, and therefore the shape this gate
    # has to catch. Without it the JSON form was invisible to both.
    # The value needs real length, or the quoted-key form turns every config entry into a
    # finding: `"pwd": "allow"` is a permission setting, and flagging it makes `--secrets-scan`
    # report a clean set as dirty and `promote` refuse it.
    ("password_assign", re.compile(r"(?i)(password|passwd|pwd)['\"]?\s*[:=]\s*['\"]?[^\s'\"]{6,}")),
    # Six, matching password_assign and the redactor's own floor. At eight there was a band of
    # values that redaction declined to rewrite and the scanner declined to report — covered by
    # neither, while the tests asserted the two met.
    (
        "api_key_assign",
        re.compile(r"(?i)(api[_-]?key|secret|token)['\"]?\s*[:=]\s*['\"]?[^\s'\"]{6,}"),
    ),
    ("bearer", re.compile(r"(?i)bearer\s+[A-Za-z0-9\-._~+/]+=*")),
]


@dataclass
class Finding:
    path: str
    rule: str
    snippet: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


#: Assignment rules whose value half needs a shape check; the rest match a fixed credential
#: format and need none.
_ASSIGNMENT_RULES = {"password_assign", "api_key_assign"}
_ASSIGNED_VALUE = re.compile(r"""['"]?\s*[:=]\s*['"]?(?P<value>[^\s'"]+)""")
#: Type names and keywords that appear as the value half of a declaration.
_CODE_TOKENS = frozenset(
    [
        "string",
        "number",
        "boolean",
        "object",
        "symbol",
        "bigint",
        "any",
        "unknown",
        "never",
        "void",
        "undefined",
        "null",
        "typeof",
        "keyof",
        "readonly",
        "str",
        "int",
        "float",
        "bool",
        "bytes",
        "dict",
        "list",
        "tuple",
        "set",
        "None",
        "True",
        "False",
        "Optional",
        "Union",
        "Any",
        "Boolean",
        "String",
        "Number",
        "Object",
    ]
)
#: A value that reaches into an expression: dotted access or a call.
_EXPRESSION_VALUE = re.compile(r"^[A-Za-z_$][\w$]*(?:\.[\w$]+|\(|\?\.)")


def _is_code_not_credential(snippet: str) -> bool:
    """Whether an assignment's value is provably code rather than a secret.

    `apiKey: string`, `apiKey = apiKey;` and `apiKey = input.apiKey?.trim()` are declarations,
    not leaks. Over a 575-case build every one of the 25 findings was of this shape, which
    makes `--secrets-scan` call a clean set dirty and `promote` refuse it.

    Deliberately narrow: only a known type token, a value echoing its own key, or something
    that continues into an expression. Rejecting *any* bare identifier would also discard
    `password = swordfish`, which is a real if weak credential. A value carrying a digit is
    never treated as code.
    """
    m = _ASSIGNED_VALUE.search(snippet)
    if not m:
        return False
    value = m.group("value").rstrip(";,)]}")
    # Checked before the digit escape: `bytes(range(256))` is a call whatever it contains, and
    # letting its digits vouch for it turned a constructor into a credential.
    if _EXPRESSION_VALUE.match(value):
        return True
    if any(c.isdigit() for c in value):
        return False
    key = re.match(r"[A-Za-z_$][\w$-]*", snippet.strip())
    if key and _same_name(value, key.group(0)):
        return True  # `apiKey = apiKey`, `API_KEY: apiKey`
    return value in _CODE_TOKENS


def _same_name(a: str, b: str) -> bool:
    """Whether two identifiers name the same thing across casing and separator style."""
    return (
        a.replace("_", "").replace("-", "").lower() == b.replace("_", "").replace("-", "").lower()
    )


def scan_text(text: str, *, path: str = "<text>") -> list[Finding]:
    out: list[Finding] = []
    for name, pat in _PATTERNS:
        for m in pat.finditer(text or ""):
            snip = m.group(0)
            if name in _ASSIGNMENT_RULES and _is_code_not_credential(snip):
                continue
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
    # Hidden tests are written into the workspace at grading time and are shipped in the case
    # file like any other content, so leaving them unscanned exempted a whole surface.
    for h in (case.get("grader") or {}).get("hidden_tests") or []:
        p = h.get("path") or "hidden"
        findings.extend(scan_text(str(h.get("content") or ""), path=f"{path}:hidden:{p}"))
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
