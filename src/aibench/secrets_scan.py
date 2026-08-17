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
    # Same shape, same reason: `sk-proj-…` stops `openai_sk` at the hyphen after `proj`, which
    # is four characters short of its floor, so the current project-key format matched nothing.
    ("openai_project_key", re.compile(r"sk-proj-[A-Za-z0-9_-]{20,}")),
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
    # `[\w-]*` on both sides because the keyword need not touch the separator: `SECRET_KEY = …`
    # matched nothing, and the redactor's own word list has the same shape, so both layers
    # missed it. Twelve files in this project's corpus are spelled that way.
    (
        "password_assign",
        re.compile(r"(?i)[\w-]*(?:password|passwd|pwd)[\w-]*['\"]?\s*[:=]\s*['\"]?[^\s'\"]{6,}"),
    ),
    # Six, matching password_assign and the redactor's own floor. At eight there was a band of
    # values that redaction declined to rewrite and the scanner declined to report — covered by
    # neither, while the tests asserted the two met.
    (
        "api_key_assign",
        re.compile(
            r"(?i)[\w-]*(?:api[_-]?key|secret|token)[\w-]*['\"]?\s*[:=]\s*['\"]?[^\s'\"]{6,}"
        ),
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

#: Rules that match a credential's actual shape rather than a keyword near a value. `bearer` is
#: excluded with the assignment rules: its character class swallows any word after `Bearer`,
#: which is a documentation phrase as often as a token.
FIXED_FORMAT_RULES = frozenset(
    {"openai_sk", "anthropic_key", "openai_project_key", "aws_key", "private_key"}
    | {"github_pat", "github_token", "gitlab_token", "slack_token", "google_api_key", "jwt"}
    | {"db_url_password"}
)
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


def scan_text(
    text: str,
    *,
    path: str = "<text>",
    rules: frozenset[str] | None = None,
) -> list[Finding]:
    """Findings in ``text``. ``rules`` restricts which patterns are applied."""
    out: list[Finding] = []
    for name, pat in _PATTERNS:
        if rules is not None and name not in rules:
            continue
        for m in pat.finditer(text or ""):
            snip = m.group(0)
            if _REDACTED in snip or text[m.end() : m.end() + 3] == _REDACTED:
                continue
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
#:
#: Matched by their exact position. Excluding the names wherever they appear would exempt any
#: subtree a case happened to give one of them — a case about a validation library, say.
#: `grader.key_lines` is NOT here: `grading.py` decides pass/fail from it and both `promote`
#: and `export-bundle` write it, so it is case content. What it needed was for the redactor's
#: own placeholder to stop reading as a credential — see `_REDACTED`.
#:
#: `metadata.validity_issues` is a runner transcript. `export_bundle` drops it before writing;
#: `promote` does not, so it does ship — but everything it quotes comes from case content that
#: is scanned in place, and reporting a gate's own failure message makes it refuse a case for
#: the words it used to explain the last refusal.
_HARNESS_RECORDS = frozenset(["metadata:validity_issues"])

#: What this pipeline writes in place of a secret. Checked on the text around a match, not just
#: inside it: `redact_source` leaves `Bearer sk-***`, and the `bearer` rule's character class
#: stops at the first `*`, so the asterisks fall outside the snippet it would report.
_REDACTED = "***"

#: Deep enough for any case — the deepest in this corpus is four. Returning "clean" at the
#: bound would be the one failure a gate must not have, so it raises instead: a scan that could
#: not finish is not a scan that found nothing.
_MAX_DEPTH = 60


def _walk_strings(node: Any, path: str, depth: int = 0) -> list[Finding]:
    """Every string *value* in the document, with the path that led to it.

    Keys are not scanned, and neither are non-string scalars. What matters is that no list of
    fields has to be extended when a field is added: `metadata.file_versions` — raw trace
    content, the one draft field written without redaction — reached the drafts with nothing
    scanning it, and three live keys sat there.
    """
    if isinstance(node, str):
        return scan_text(node, path=path)
    if depth >= _MAX_DEPTH:
        raise ValueError(f"document nests deeper than {_MAX_DEPTH} at {path!r}; refusing to guess")
    out: list[Finding] = []
    if isinstance(node, dict):
        # A file entry names itself. Reporting `context:files[3]:content` makes a human open the
        # JSON to learn which file; reporting the path it carries does not.
        own = node.get("path") if isinstance(node.get("path"), str) else None
        for k, v in node.items():
            here = f"{path}:{k}" if path else str(k)
            if here.split(":", 1)[-1] in _HARNESS_RECORDS:
                continue
            out.extend(
                _walk_strings(v, f"{here}({own})" if own and k != "path" else here, depth + 1)
            )
    elif isinstance(node, list):
        for i, v in enumerate(node):
            out.extend(_walk_strings(v, f"{path}[{i}]", depth + 1))
    return out


def scan_case_dict(case: dict[str, Any], *, path: str = "case") -> list[Finding]:
    return _walk_strings(case, path)


def scan_case_file(path: Path) -> list[Finding]:
    return scan_case_dict(load_json(path), path=str(path.name))


#: Files a content scan cannot read as text, and would only produce noise from.
_BINARY_SUFFIXES = frozenset(
    {".png", ".jpg", ".jpeg", ".gif", ".pdf", ".zip", ".tar", ".gz", ".whl", ".so", ".dylib"}
)

#: A file that declares itself a fixture. Three test modules exist to prove this scanner catches
#: each credential format, and `.env.example` is a template whose whole job is to show the shape
#: of a connection string — so the hook reporting them is the scanner finding its own bait.
#:
#: A marker rather than a path allowlist: a list in this module goes stale the moment someone
#: adds a fixture, and the failure mode of a stale list is that the hook blocks a legitimate
#: commit until it is edited, which is how a hook gets disabled. The marker also only reaches
#: :func:`scan_paths`; case JSON always goes through the full case scan, so a generated case
#: that happened to contain the string cannot exempt itself.
#:
#: It must sit in the file's header. As "anywhere in the file" it was an exemption any *later*
#: line could claim — a real credential 400 lines below a fixture block was silently exempt,
#: and so was one pasted into a file that merely quotes the marker.
SYNTHETIC_MARKER = "aibench: synthetic-secrets"

#: How far into a file the declaration may sit. All four tracked fixtures — `.env.example`
#: and three test modules — declare it on line 2 or 3.
_MARKER_HEADER_LINES = 5


def declares_synthetic_secrets(text: str) -> bool:
    """Whether ``text`` declares itself a credential fixture, in its header."""
    return any(SYNTHETIC_MARKER in ln for ln in text.splitlines()[:_MARKER_HEADER_LINES])


def scan_paths(paths: list[Path]) -> dict[str, Any]:
    """Scan arbitrary files, for the pre-commit hook.

    A case JSON is scanned as a case, so the full rule set and the field-position exemptions
    both apply. Anything else — source, docs, configs — is scanned with the fixed-format rules
    only.

    That narrowing is deliberate and it is what makes the hook survivable. `password_assign`
    and `api_key_assign` match a *keyword near a value*, which is the right shape for a case's
    JSON strings and the wrong shape for a repository that talks about credentials for a
    living: run over this tree they report `max_tokens={...}`, `api_key_env: OPENAI_API_KEY`,
    and this module's own docstrings. A hook that cries wolf on every commit gets disabled, and
    a disabled hook catches nothing. The fixed-format rules — `sk-`, `sk-ant-`, `sk-proj-`,
    AWS, private keys, GitHub, GitLab, Slack, Google, JWT, DB URLs with a password — match a
    credential's actual shape and do not have that failure mode.

    The heuristics are not lost: they still run over every case, which is where the corpus
    lives and where `--secrets-scan`, `promote` and `export-bundle` all apply them.
    """
    from aibench.cases import is_case_json_path

    all_f: list[dict[str, Any]] = []
    scanned = 0
    for p in paths:
        if not p.is_file() or p.suffix.lower() in _BINARY_SUFFIXES:
            continue
        scanned += 1
        findings = None
        if p.suffix == ".json" and is_case_json_path(p):
            # `is_case_json_path` is a filename test, so `package.json`, `tsconfig.json` and a
            # half-written case all reach here. Parsing is where it used to die: one malformed
            # JSON anywhere in the commit took the whole hook down with a `JSONDecodeError`,
            # and a hook that crashes is a hook that gets removed.
            try:
                findings = scan_case_file(p)
            except (OSError, ValueError):
                findings = None
        if findings is None:
            try:
                text = p.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            if declares_synthetic_secrets(text):
                continue
            findings = scan_text(text, path=str(p), rules=FIXED_FORMAT_RULES)
        all_f.extend(f.to_dict() for f in findings)
    return {
        "files_scanned": scanned,
        "finding_count": len(all_f),
        "clean": len(all_f) == 0,
        "findings": all_f,
    }


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
