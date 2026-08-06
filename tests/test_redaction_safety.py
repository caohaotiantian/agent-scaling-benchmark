"""Redaction must not turn working source into source that cannot be parsed.

`(...)\\s*[:=]\\s*\\S+` swallowed the delimiter closing the value, so
`assert "token=abc" in url` became `assert "token=*** in url` — an unterminated string
literal. Measured over `drafts-from-db` before the fix: 56 lines across 90 files in 45 drafts
were left with an unbalanced quote. A case built from one of those shipped with a reference
solution and a hidden test that could not be imported, and then failed every configuration
equally, which reads as a hard case rather than a broken one.

Those stored drafts are not repaired by this change — the damage is already in the files. This
only stops new drafts acquiring it.
"""

import ast

import pytest

from aibench.extract.sessions import redact_secrets, redact_source
from aibench.secrets_scan import scan_case_dict


class TestTheValueGoesButTheSyntaxStays:
    @pytest.mark.parametrize(
        ("source", "secret"),
        [
            ('assert "token=abc123" in ws_url', "abc123"),
            ('ws_url += "?token=secretvalue"', "secretvalue"),
            ('ctx = _make_ctx("read_file", "API_KEY=sk-abcdefghij")', "sk-abcdefghij"),
            ("config = {'password': 'hunter2'}", "hunter2"),
            ('token = "mytok123"', "mytok123"),
            ('return f"{base}/x?access_token=abc"', "?access_token=abc"),
            ('{"api_key": "xyz123"}', "xyz123"),
        ],
    )
    def test_the_secret_is_removed_and_the_line_still_parses(self, source, secret):
        out = redact_secrets(source)
        assert secret not in out
        assert "***" in out
        ast.parse(out)  # raises SyntaxError if redaction broke the line

    def test_quotes_stay_balanced(self):
        out = redact_secrets('assert "token=abc123" in ws_url, ("boom")')
        assert out.count('"') % 2 == 0
        assert out == 'assert "token=***" in ws_url, ("boom")'


class TestExpressionsAreLeftAlone:
    """A value that continues into an expression holds no secret; rewriting only breaks it."""

    @pytest.mark.parametrize(
        "source",
        [
            'api_key = os.environ["OPENAI_API_KEY"]',
            "token = settings.SECRET_TOKEN",
            "password = get_password()",
            "secret = config[key]",
        ],
    )
    def test_untouched(self, source):
        assert redact_secrets(source) == source


class TestSourceIsGuardedByParsing:
    """`redact_secrets` is deliberately generous; `redact_source` is what keeps code working.

    Deciding from the value alone whether an unquoted right-hand side is a credential or code
    is hopeless, and guessing conservatively means shipping secrets. So the pattern reaches
    wide and a parse check settles it: a rewrite that breaks the file is dropped.
    """

    @pytest.mark.parametrize(
        "source",
        [
            "token = None",
            "api_key = DEFAULT",
            'self.password = password or b""',
            'url = "https://h/x?token=" + tok + "&u=" + user',
            "password: SecretStr",
            'TOKEN = """abc"""',
            "token: str",
            "secret: bool",
            'def read(self) -> Token:\n    """Consume the next token."""\n',
        ],
    )
    def test_a_rewrite_that_would_break_the_file_is_dropped(self, source):
        out = redact_source(source, path="x.py")
        ast.parse(out)
        assert out == source

    def test_one_unsafe_line_does_not_discard_the_safe_ones(self):
        # Reverting the whole file over a single bad line would leave every other secret in it.
        src = 'token = None\nAPI_KEY = "sk-abcdefghijklmnop"\n'
        out = redact_source(src, path="x.py")
        ast.parse(out)
        assert "sk-abcdefghijklmnop" not in out
        assert "token = None" in out

    def test_a_file_with_no_registered_language_is_still_redacted(self):
        # No parser means no veto; a YAML or markdown value is redacted as before.
        out = redact_source("api_key: a1b2c3d4\n", path="config.yaml")
        assert "a1b2c3d4" not in out

    def test_a_safe_rewrite_is_kept(self):
        out = redact_source('API_KEY = "sk-abcdefghijklmnop"\n', path="x.py")
        ast.parse(out)
        assert "sk-abcdefghijklmnop" not in out


class TestScanCoverage:
    def test_hidden_tests_are_scanned(self):
        # Hidden tests are shipped in the case file and written into the workspace at grading
        # time; leaving them unscanned exempted a whole surface from the gate.
        case = {
            "prompt": "",
            "context": {"files": []},
            "grader": {
                "gold_files": [],
                "hidden_tests": [
                    {"path": "test_h.py", "content": 'API_KEY = "sk-abcdefghijklmnop"\n'}
                ],
            },
        }
        findings = scan_case_dict(case)
        assert findings, "a secret in a hidden test must be reported"
        assert any("hidden" in f.path for f in findings)

    def test_a_clean_case_reports_nothing(self):
        case = {
            "prompt": "fix the bug",
            "context": {"files": [{"path": "a.py", "content": "x = 1\n"}]},
            "grader": {"gold_files": [], "hidden_tests": []},
        }
        assert scan_case_dict(case) == []
