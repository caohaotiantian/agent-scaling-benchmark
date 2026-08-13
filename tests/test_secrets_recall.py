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

from aibench.extract.llm_chat_records import record_to_case_draft
from aibench.secrets_scan import scan_case_dict, scan_text

#: One live-shaped credential per format the scanner is expected to know. The values are
#: synthetic but structurally exact — a rule that matches a shortened stand-in and misses the
#: real thing is the failure this file is here to prevent.
CREDENTIALS = {
    "openai_sk": "sk-REDACTED",
    "anthropic_key": "sk-ant-REDACTED",
    "github_pat": "github_pat_REDACTED",
    "github_token": "ghp_REDACTED",
    "gitlab_token": "glpat-AbCdEfGhIjKlMnOpQrSt",
    "slack_token": "xoxb-123456789012-1234567890123-AbCdEfGhIjKlMnOpQrStUvWx",
    "google_api_key": "AIzaSyA1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6Q",
    "aws_key": "AKIAIOSFODNN7EXAMPLE",
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

    def test_the_harness_own_records_are_not_case_content(self):
        """`key_lines` can hold the redactor's own `sk-***` placeholder and `validity_issues`
        holds verbatim runner output. Reporting either makes the gate refuse a case for text it
        produced itself — and `export_bundle` deletes `validity_issues` before writing anyway."""
        case = {
            "prompt": "fix it",
            "context": {"files": []},
            "grader": {"key_lines": ["headers = {'Authorization': 'Bearer sk-***'}"]},
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


class TestDraftsAreBuiltWithoutRawCredentials:
    def test_file_versions_is_redacted_before_it_reaches_disk(self):
        """Reporting a key still leaves it in the file. This is the half that removes it."""
        body = f"API_KEY = '{CREDENTIALS['openai_sk']}'\nPASSWORD = 'buqgupvifauabbfc'\n"
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
                                "oldString": "PASSWORD = 'buqgupvifauabbfc'",
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
        assert "buqgupvifauabbfc" not in blob
