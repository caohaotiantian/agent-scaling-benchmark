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
            ('ws_url += "?token=s3cretvalue"', "s3cretvalue"),
            ('ctx = _make_ctx("read_file", "API_KEY=sk-abcdefghij")', "sk-abcdefghij"),
            ("config = {'password': 'hunter2'}", "hunter2"),
            ('token = "mytok123"', "mytok123"),
            ('return f"{base}/x?access_token=a1b2c3"', "access_token=a1b2c3"),
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


class TestTheCoverageBoundaryIsDeliberate:
    """What redaction declines to touch, the scanner must still catch.

    The wide pattern is only justified where a parse check can veto it. Everywhere else — a
    language with no parser, a chat fragment that never parsed, text inside a docstring — the
    conservative rule applies and some short secrets go unrewritten. That is survivable only
    because `secrets_scan` is the gate that blocks publication, so it has to catch them.
    """

    def test_a_short_alphabetic_secret_is_left_to_the_scanner(self):
        from aibench.secrets_scan import scan_text

        src = "password = swordfish\n"
        assert redact_source(src, path="x.py") == src, "conservative rule leaves it"
        assert scan_text(src, path="t"), "so the gate must report it"

    @pytest.mark.parametrize("name", ["token", "api_key", "secret", "password", "passwd", "pwd"])
    @pytest.mark.parametrize("value", ["a1b2c3", "s3cretv", "hunter22", "abcdefgh"])
    def test_every_six_plus_value_is_caught_by_one_of_the_two(self, name, value):
        """Walk the band rather than pick one string, so the two floors cannot drift apart.

        They already did: the redactor took six characters while the scanner took eight, and
        the class below asserted they met. Values shorter than six are covered by neither and
        that is accepted — a five-character credential is not one.
        """
        from aibench.secrets_scan import scan_text

        src = f"{name} = {value}\n"
        redacted = redact_source(src, path="cfg.txt") != src
        scanned = bool(scan_text(src, path="t"))
        assert redacted or scanned, f"{src!r} is neither redacted nor scanned"

    def test_javascript_gets_the_conservative_rule_not_the_wide_one(self):
        # `parses` cannot judge JS, so there is no veto and the wide pattern would run
        # unchecked — it broke 17 of 19 real .js files it touched.
        src = "proto.onToken = function (token, value) {\n  return value;\n};\n"
        assert redact_source(src, path="a.js") == src

    def test_an_unparseable_fragment_gets_the_conservative_rule(self):
        # 78% of real draft .py content is a fragment that never parsed, so the guard cannot
        # fire there and the wide pattern would apply raw.
        src = "def handler(req):\n    token = None\n    return token\nif True\n    pass\n"
        assert redact_source(src, path="frag.py") == src

    def test_a_private_key_is_redacted_even_when_another_line_is_vetoed(self):
        # The PEM pattern spans lines, so the line-by-line salvage can never match it; applying
        # it outside that loop is what stops a vetoed file keeping its key.
        src = (
            "token = None\n"
            'KEY = """-----BEGIN RSA PRIVATE KEY-----\n'
            "MIIEowIBAAKCAQEAxxxx\n"
            '-----END RSA PRIVATE KEY-----"""\n'
        )
        out = redact_source(src, path="k.py")
        assert "MIIEowIBAAKCAQEAxxxx" not in out
        assert "BEGIN RSA PRIVATE KEY" not in out
        assert "token = None" in out


class TestScannerPrecision:
    """Accepting a quoted key must not turn every config entry into a finding."""

    def test_a_config_permission_is_not_a_password(self):
        from aibench.secrets_scan import scan_text

        # `"pwd": "allow"` is opencode's working-directory permission. Flagging it made
        # --secrets-scan report a clean generated set as dirty (12 hits in a 21-case batch).
        assert scan_text('  "pwd": "allow",', path="t") == []
        assert scan_text('"pwd": "deny"', path="t") == []

    def test_a_real_password_in_a_quoted_key_is_still_found(self):
        from aibench.secrets_scan import scan_text

        assert scan_text('{"password": "hunter2"}', path="t")
        assert scan_text("password = swordfish", path="t")


class TestTheNameMustBeAWholeWord:
    """`Token` inside `CancellationToken` is not a secret name.

    Without a left boundary the rule broke 12 of the 20 real .js files it touched — and JS has
    no parser here, so nothing vetoed it.
    """

    def test_a_name_suffix_is_not_a_match(self):
        for src in (
            "})(CancellationToken || (exports.CancellationToken = CancellationToken = {}));",
            "exports.createScalarToken = createScalarToken;",
            "var spaceAfterMeaningfulToken = false;",
        ):
            assert redact_source(src, path="a.js") == src

    def test_an_underscore_prefix_is_still_a_match(self):
        # ZOTERO_API_KEY and GITHUB_PERSONAL_ACCESS_TOKEN appear in the real drafts.
        assert (
            redact_source("ZOTERO_API_KEY=abc123def\n", path="x.env")
            != "ZOTERO_API_KEY=abc123def\n"
        )

    def test_a_bare_identifier_value_is_not_a_credential(self):
        assert (
            redact_source("token = createScalarToken\n", path="a.js")
            == "token = createScalarToken\n"
        )

    def test_a_plain_integer_is_not_a_credential(self):
        assert (
            redact_source("class P { token=1048576; }", path="a.js") == "class P { token=1048576; }"
        )


class TestThePemRuleIsNotExempt:
    def test_a_module_that_merely_mentions_both_markers_is_untouched(self):
        # Unanchored, the pattern ran from any BEGIN to the next END and deleted the code
        # between them — and when that code was a whole function the result still parsed.
        src = (
            'd = {"a": "-----BEGIN RSA PRIVATE KEY-----"}\ne = ("-----END RSA PRIVATE KEY-----")\n'
        )
        out = redact_source(src, path="x.py")
        ast.parse(out)
        assert out == src

    def test_a_real_key_is_still_removed(self):
        src = (
            "token = None\n"
            'KEY = """-----BEGIN RSA PRIVATE KEY-----\n'
            "MIIEowIBAAKCAQEAxxxx\n"
            '-----END RSA PRIVATE KEY-----"""\n'
        )
        out = redact_source(src, path="k.py")
        ast.parse(out)
        assert "MIIEowIBAAKCAQEAxxxx" not in out


class TestTheTwoHalvesAgree:
    """What the gate declines to call a secret, the redactor must not destroy.

    They disagreed for one commit: the scanner was taught that `"pwd": "allow"` is a
    working-directory permission rather than a password, and the redactor was taught the
    opposite in the next commit. It rewrote 44 lines of real draft config.
    """

    @pytest.mark.parametrize(
        "text",
        [
            '  "pwd": "allow",',
            '"pwd": "deny"',
            '{"api_key": "xyz123"}',
            "config = {'password': 'hunter2'}",
            'token = "mytok123"',
            "ZOTERO_API_KEY=abc123def",
        ],
    )
    def test_redactor_and_scanner_reach_the_same_verdict(self, text):
        from aibench.secrets_scan import scan_text

        rewrites = redact_source(text, path="o.json") != text
        flags = bool(scan_text(text, path="t"))
        assert rewrites == flags, f"redactor={rewrites} scanner={flags} for {text!r}"


class TestKnownBounds:
    """Documented gaps. Each is zero-occurrence on the real corpus and caught by the scanner."""

    def test_a_bare_python_value_is_never_rewritten(self):
        # `***` is not an expression, so the veto always drops it — the aggressive path is
        # quoted-values-only for Python, which the pattern alone does not reveal.
        from aibench.secrets_scan import scan_text

        src = "GITLAB_TOKEN=abc123def456\n"
        assert redact_source(src, path="c.py") == src
        assert redact_source(src, path="c.env") != src, "but a non-Python path is redacted"
        assert scan_text(src, path="t"), "and the gate catches the Python case"

    def test_a_camelcase_name_is_not_matched_but_is_scanned(self):
        from aibench.secrets_scan import scan_text

        src = 'accessToken = "ghp_abc123def456"\n'
        assert redact_source(src, path="c.py") == src
        assert scan_text(src, path="t")
