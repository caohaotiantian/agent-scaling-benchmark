"""Scan case JSON / text for likely secrets before publish or after generate."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from aibench.io_util import load_json

_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("openai_sk", re.compile(r"sk-[A-Za-z0-9]{20,}")),
    # `sk-ant-` values contain hyphens, which `openai_sk`'s character class stops at, so it
    # matches only the first fragment and the length floor can then miss it entirely.
    ("anthropic_key", re.compile(r"sk-ant-[A-Za-z0-9_-]{20,}")),
    ("aws_key", re.compile(r"(?:AKIA|ASIA)[0-9A-Z]{16}")),
    ("private_key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    # Each of these was found in this project's own corpus with no rule to match it. The
    # `github_pat` sat in a draft's `prompt` — a field the scanner has always read — which is
    # why coverage is two separate problems: a field this does not reach, and a format it does
    # not know. Widening one never fixes the other.
    ("github_pat", re.compile(r"github_pat_[A-Za-z0-9_]{50,}")),
    ("github_token", re.compile(r"gh[pousr]_[A-Za-z0-9]{36,}")),
    ("gitlab_token", re.compile(r"glpat-[A-Za-z0-9_-]{20}")),
    ("slack_token", re.compile(r"xox[abpres]-[A-Za-z0-9-]{10,}")),
    ("google_api_key", re.compile(r"AIza[A-Za-z0-9_-]{35}")),
    # Header and payload both, so a lone base64 word cannot pass as one.
    ("jwt", re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]+")),
    (
        "db_url_password",
        re.compile(
            r"(?:postgres|postgresql|mysql|mongodb|redis|amqp)(?:\+\w+)?://[^:/\s]+:[^@\s]{6,}@"
        ),
    ),
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


#: Text the harness wrote about a case, not text the case ships. `key_lines` can hold the
#: redactor's own `sk-***` placeholder, and `validity_issues` holds verbatim runner output —
#: which `export_bundle` deletes before writing anyway. Reporting either makes the gate refuse
#: a case for something it produced itself.
_HARNESS_RECORDS = frozenset(["key_lines", "validity_issues"])


def _walk_strings(node: Any, path: str) -> list[Finding]:
    """Every string in the document, with the path that led to it.

    An explicit list of fields has to be extended whenever a field is added, and once was not:
    `metadata.file_versions` — raw trace content, the one draft field written without
    redaction — reached the drafts with nothing scanning it, and five live keys sat there.
    Walking removes the step that gets skipped.
    """
    out: list[Finding] = []
    if isinstance(node, str):
        return scan_text(node, path=path)
    if isinstance(node, dict):
        for k, v in node.items():
            if k in _HARNESS_RECORDS:
                continue
            out.extend(_walk_strings(v, f"{path}:{k}" if path else str(k)))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            out.extend(_walk_strings(v, f"{path}[{i}]"))
    return out


def scan_case_dict(case: dict[str, Any], *, path: str = "case") -> list[Finding]:
    return _walk_strings(case, path)


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
