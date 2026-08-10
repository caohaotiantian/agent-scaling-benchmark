"""The adapter that measures the model rather than the harness around it.

Measured on the same 31 cases, the scaffold was the dominant variable: it flipped 32.3% of
GLM-5.1's cases between identical repeats and reversed the ranking against Deepseek-V4-Flash.
Removing it took GLM-5.1's flip rate to 0.0%.
"""

from aibench.agents.bare_model import BareModelAgent, extract_code
from aibench.models import AgentConfig, Case, ModelConfig

CASE = Case.from_dict(
    {
        "case_id": "c1",
        "schema_version": "0.1",
        "task_type": "bugfix",
        "language": "python",
        "prompt": "Values below the lower bound come back unchanged.",
        "context": {
            "files": [
                {
                    "path": "clamp.py",
                    "content": "def clamp(v, lo, hi):\n    return min(v, hi)\n",
                    "role": "impl",
                },
                {
                    "path": "test_clamp.py",
                    "content": "def test_low():\n    assert True\n",
                    "role": "test",
                },
            ]
        },
        "grader": {"mode": "script", "command": "python -m pytest -q", "gold_files": []},
        "metadata": {},
    }
)


def _agent(**opts):
    ac = AgentConfig.from_dict(
        {"name": "bare", "version": "1", "adapter": "bare_model", "options": opts}
    )
    mc = ModelConfig.from_dict({"name": "m", "provider": "openai_compat", "model": "M"})
    return BareModelAgent(ac, mc)


def test_the_fenced_block_is_the_answer():
    assert extract_code("```python\nx = 1\n```") == "x = 1"
    assert extract_code("Here:\n```\nx = 1\n```\nDone.") == "x = 1"


def test_the_largest_block_wins():
    """Models often quote the offending lines first; the file is the long block."""
    text = "The bug is here:\n```\nreturn min(v, hi)\n```\nFixed file:\n```python\nimport sys\n\n\ndef clamp(v, lo, hi):\n    return max(lo, min(v, hi))\n```"
    assert "def clamp" in extract_code(text)
    assert extract_code(text).count("\n") > 2


def test_an_unfenced_reply_is_still_used():
    assert extract_code("def clamp(v, lo, hi):\n    return v\n").startswith("def clamp")


def test_the_prompt_carries_the_file_and_the_visible_tests():
    _path, user = _agent()._prompt(CASE)
    assert "def clamp" in user
    assert "def test_low" in user, "visible tests make the task well posed"
    assert "clamp.py" in user


def test_tests_can_be_withheld():
    _path, user = _agent(show_tests=False)._prompt(CASE)
    assert "def clamp" in user
    assert "def test_low" not in user


def test_a_case_with_no_impl_file_fails_rather_than_guessing(tmp_path):
    case = Case.from_dict(
        {
            "case_id": "c2",
            "schema_version": "0.1",
            "task_type": "bugfix",
            "language": "python",
            "prompt": "p",
            "context": {"files": []},
            "grader": {"mode": "script", "command": "python -m pytest -q", "gold_files": []},
            "metadata": {},
        }
    )
    assert _agent()._prompt(case) is None


def test_the_budget_defaults_leave_room_to_reason():
    """8192 scored 'did the budget suffice'; 11 of GLM-5.2's 16 failures were truncation."""
    a = _agent()
    assert a.max_tokens >= 16384
    assert a.max_token_ceiling > a.max_tokens


def test_it_is_registered_under_its_own_name():
    from aibench.agents.registry import create_agent

    ac = AgentConfig.from_dict({"name": "b", "version": "1", "adapter": "bare_model"})
    mc = ModelConfig.from_dict({"name": "m", "provider": "openai_compat", "model": "M"})
    assert isinstance(create_agent(ac, mc), BareModelAgent)
