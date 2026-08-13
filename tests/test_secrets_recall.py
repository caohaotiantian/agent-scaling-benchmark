"""What the scanner must catch, pinned by shape rather than by the corpus of the day.

This file exists because a real OpenAI key, a GitHub personal access token and a database URL
carrying a password reached a draft pool that every gate reported as clean. The two failures
were different in kind and neither was covered by the other:

* The PAT sat in `prompt` — a field the scanner has always read — and went unreported because
  there was no rule for its format. Scanning more fields would not have found it.
* The keys sat in `metadata.file_versions[].pre/post`, which is raw trace content: the only
  field of a draft that `record_to_case_draft` writes without redacting, and the only one
  `scan_case_dict` did not read. Adding a rule would not have found those.

Fixtures are inline. A test that reads a generated case set only passes on a machine that
happened to build it, which this repository has already learned once.
"""

import json
from types import SimpleNamespace
from typing import ClassVar

from aibench.extract.llm_chat_records import record_to_case_draft
from aibench.secrets_scan import scan_case_dict, scan_text

#: One credential per format the scanner must know: invented values in the exact shape of the
#: real thing, because a rule tuned to a shortened stand-in misses the real thing.
#:
#: Every value here is fabricated. An earlier draft of this file reached for the corpus and
#: pasted four values lifted from it — one of which git had never seen before — which would
#: have put a credential into tracked history in the commit that exists to keep them out.
CREDENTIALS = {
    "openai_sk": "sk-REDACTED",
    "anthropic_key": "sk-ant-REDACTED",
    "github_pat": "github_pat_REDACTED",
    "github_token": "ghp_REDACTED",
    "gitlab_token": "glpat-AbCdEfGhIjKlMnOpQrSt",
    "slack_token": "xoxb-123456789012-1234567890123-AbCdEfGhIjKlMnOpQrStUvWx",
    "google_api_key": "AIzaSyA1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6Q",
    "aws_key": "AKIAIOSFODNN7EXAMPLE",
    # The temporary-credential prefix. Without it, reverting the rule to `AKIA` only
    # leaves the whole suite green.
    "aws_temp_key": "ASIAZZFAKE0000FAKE00",
    "jwt": "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dBjftJeZ4CVPmB92K27uhbUJU1p1r_wW1g",
    "db_url_password": "mysql+pymysql://root:rootpassword123@10.0.0.1:3306/app",
}


class TestEveryKnownFormatIsCaught:
    def test_each_credential_format_is_reported(self):
        missed = [name for name, value in CREDENTIALS.items() if not scan_text(value)]
        assert missed == [], f"no rule matches these credential formats: {missed}"

    def test_a_pat_in_the_prompt_is_reported(self):
        """The field was always scanned; the format had no rule. Walking more fields cannot fix
        that, which is why coverage is two problems and not one."""
        case = {
            "prompt": f"{CREDENTIALS['github_pat']} 这是我的 github token，帮我拉一下仓库",
            "context": {"files": []},
            "grader": {},
        }
        assert [f.rule for f in scan_case_dict(case)] == ["github_pat"]


class TestTheFieldsCarryingRawTraceContent:
    def _draft_shaped(self, body):
        return {
            "prompt": "修一下配置读取",
            "context": {"files": [{"path": "a.py", "content": "x = 1\n"}]},
            "grader": {},
            "metadata": {"file_versions": [{"path": "settings.py", "pre": body, "post": body}]},
        }

    def test_a_key_in_file_versions_is_reported(self):
        """Five real keys lived here. It is the one draft field written without redaction and
        the one the scanner did not read, so nothing saw them."""
        case = self._draft_shaped(f"API_KEY = '{CREDENTIALS['openai_sk']}'\n")
        assert any(f.rule == "openai_sk" for f in scan_case_dict(case))

    def test_a_runner_transcript_is_not_case_content(self):
        """`validity_issues` quotes the case back at itself to explain a refusal. Reporting it
        makes the gate refuse a case for the words it used to describe the last refusal, and
        everything quoted there is scanned where it actually lives."""
        case = {
            "prompt": "fix it",
            "context": {"files": []},
            "grader": {},
            "metadata": {
                "validity_issues": [
                    {
                        "code": "solvability_gate",
                        "message": "- 'Bearer admin-token'\n+ 'Bearer token'",
                    }
                ]
            },
        }
        assert scan_case_dict(case) == []

    def test_the_exclusion_is_positional_not_by_name(self):
        """A case that ships a file under a key called `key_lines` is still case content."""
        case = {
            "prompt": "fix it",
            "context": {
                "files": [{"path": "a.py", "key_lines": f"K = '{CREDENTIALS['openai_sk']}'"}]
            },
            "grader": {},
        }
        assert any(f.rule == "openai_sk" for f in scan_case_dict(case))


class TestDraftsAreBuiltWithoutRawCredentials:
    def test_file_versions_is_redacted_before_it_reaches_disk(self):
        """Reporting a key still leaves it in the file. This is the half that removes it."""
        body = f"API_KEY = '{CREDENTIALS['openai_sk']}'\nPASSWORD = 'zzfakefakefakefk'\n"
        read = {
            "role": "tool",
            "content": (
                f"<path>settings.py</path>\n<type>file</type>\n<content>{body}"
                f"\n(End of file - total {len(body.splitlines())} lines)</content>"
            ),
            "tool_calls": None,
        }
        edit = {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "function": {
                        "name": "edit",
                        "arguments": json.dumps(
                            {
                                "filePath": "settings.py",
                                "oldString": "PASSWORD = 'zzfakefakefakefk'",
                                "newString": "PASSWORD = os.environ['PW']",
                            }
                        ),
                    }
                }
            ],
        }

        rec = SimpleNamespace(
            request_id="chatcmpl-test",
            start_time=None,
            model="m",
            requests_tags="User-Agent: opencode/1.0",
            tools=[],
            full_history=[{"role": "user", "content": "帮我把密码改成读环境变量"}, read, edit],
            key_alias=None,
        )
        draft = record_to_case_draft(rec)
        assert draft is not None
        fvs = (draft.get("metadata") or {}).get("file_versions") or []
        assert fvs, "the fixture must actually produce a pair, or this test proves nothing"
        blob = json.dumps(fvs, ensure_ascii=False)
        assert CREDENTIALS["openai_sk"] not in blob
        assert "zzfakefakefakefk" not in blob


class TestTheKeywordNeedNotTouchTheSeparator:
    """`SECRET_KEY = "..."` matched nothing: the rule wanted the keyword immediately before the
    `=`, and here `_KEY` sits between. The redactor's own word list has the same shape, so both
    layers missed it — 12 files in this corpus carry that spelling.
    """

    LEAKS: ClassVar[list[str]] = [
        'SECRET_KEY = "hardcoded-secret-key-12345"',
        'JWT_SECRET_KEY = "zzfakefakefake00"',
        'ACCESS_TOKEN_ID = "zzfake000111222"',
        'MY_API_KEY_PROD = "zzfake000111222"',
    ]

    def test_a_keyword_inside_a_longer_name_is_still_a_credential(self):
        missed = [line for line in self.LEAKS if not scan_text(line)]
        assert missed == []


class TestWhatMustNotBeReported:
    """Every floor and every negative side, because loosening a rule is invisible otherwise:
    each format is pinned by one positive string, and nothing stops a bound from drifting.
    """

    NOT_CREDENTIALS: ClassVar[list[str]] = [
        "sk-tooshort",  # openai_sk floor
        "ghp_short",  # github_token floor
        "github_pat_tooshort",  # github_pat floor
        "glpat-short",  # gitlab_token length
        "AIzaTooShort",  # google_api_key length
        "eyJhbGciOiJIUzI1NiJ9.notbase64payload",  # jwt needs both segments
        "AKIAshortlower",  # aws_key charset
        "postgres://user@host/db",  # db url with no password
        # The redactor's own output. Reporting it makes the gate refuse a case for text this
        # very pipeline wrote, and `key_lines` carries exactly this shape.
        "headers = {'Authorization': 'Bearer sk-***'}",
        "API_KEY = '***'",
        "password = ***",
    ]

    def test_none_of_these_is_reported(self):
        reported = [s for s in self.NOT_CREDENTIALS if scan_text(s)]
        assert reported == []


class TestGraderKeyLinesIsCaseContent:
    """`grading.py` decides pass/fail from `key_lines`, and both `promote` and `export-bundle`
    write it. Excluding it from the scan was wrong: what it needed was for the redactor's
    placeholder to stop reading as a credential.
    """

    def test_a_credential_in_key_lines_is_reported(self):
        case = {
            "prompt": "fix it",
            "context": {"files": []},
            "grader": {"key_lines": [f"API_KEY = '{CREDENTIALS['openai_sk']}'"]},
        }
        assert any(f.rule == "openai_sk" for f in scan_case_dict(case))
