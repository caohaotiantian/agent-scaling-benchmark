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

from aibench.extract.sessions import redact_secrets
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
    """A value that is an expression holds no secret, and rewriting it only breaks the file."""

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


class TestAnnotationsAreNotAssignments:
    """`token: str` declares a type; it is not a leak, and mangling it breaks the module."""

    @pytest.mark.parametrize("source", ["token: str", "secret: bool", "api_key: int"])
    def test_a_type_annotation_survives(self, source):
        assert redact_secrets(source) == source
        ast.parse(redact_secrets(source))

    @pytest.mark.parametrize("source", ["api_key: a1b2c3d4", "token: longenoughvalue"])
    def test_a_secret_looking_value_behind_a_colon_is_still_redacted(self, source):
        assert "***" in redact_secrets(source)


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
