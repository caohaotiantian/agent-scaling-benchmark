from aibench.models import Case
from aibench.validity import audit_case, audit_case_set, case_fingerprint


def _case(**kwargs) -> Case:
    base = {
        "case_id": "v-test",
        "schema_version": "0.1",
        "task_type": "feature",
        "language": "python",
        "prompt": "Implement function add(a, b) returning sum of two integers in add.py",
        "context": {
            "files": [
                {"path": "add.py", "content": "def add(a, b):\n    raise NotImplementedError\n"},
                {
                    "path": "test_add.py",
                    "content": "from add import add\n\ndef test_add():\n    assert add(1, 2) == 3\n",
                },
            ]
        },
        "grader": {"mode": "script", "command": "python -m pytest -q test_add.py"},
        "metadata": {},
    }
    base.update(kwargs)
    if "context" in kwargs:
        base["context"] = kwargs["context"]
    if "grader" in kwargs:
        base["grader"] = kwargs["grader"]
    return Case.from_dict(base)


def test_stub_fail_gate_passes_on_broken_stub():
    r = audit_case(_case())
    assert r.ok is True
    assert r.checks["stub_fail"]["ok"] is True
    assert r.difficulty in {"easy", "medium", "hard"}


def test_stub_fail_gate_detects_already_correct():
    c = _case(
        context={
            "files": [
                {"path": "add.py", "content": "def add(a, b):\n    return a + b\n"},
                {
                    "path": "test_add.py",
                    "content": "from add import add\n\ndef test_add():\n    assert add(1, 2) == 3\n",
                },
            ]
        }
    )
    r = audit_case(c)
    assert r.ok is False
    assert any(i.code == "stub_fail_gate" for i in r.issues)


def test_contamination_keyline():
    c = _case(
        grader={
            "mode": "gold",
            "match": "contains_key_lines",
            "key_lines": ["def add(a, b):", "return a + b"],
            "gold_files": [],
        },
        context={
            "files": [
                {
                    "path": "add.py",
                    "content": "def add(a, b):\n    return a + b\n",
                }
            ]
        },
    )
    r = audit_case(c)
    assert any("contamination" in i.code for i in r.issues)


def test_fingerprint_stable():
    c1 = _case()
    c2 = _case()
    assert case_fingerprint(c1) == case_fingerprint(c2)


def test_audit_seed_fixture_set():
    # seed-v0 lives in tests fixtures; should be loadable
    rep = audit_case_set("seed-v0")
    assert rep["total"] >= 3
    assert "content_fingerprint" in rep
