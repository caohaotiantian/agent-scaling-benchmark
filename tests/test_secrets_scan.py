from aibench.secrets_scan import scan_case_dict, scan_text


def test_scan_detects_sk():
    findings = scan_text("token sk-abcdefghijklmnopqrstuvwxyz0123456789")
    assert any(f.rule == "openai_sk" for f in findings)


def test_scan_case_clean():
    case = {
        "prompt": "实现 add",
        "context": {"files": [{"path": "a.py", "content": "def add(a,b): return a+b\n"}]},
        "grader": {},
    }
    assert scan_case_dict(case) == []
