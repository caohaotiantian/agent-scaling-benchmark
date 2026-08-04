"""Model/base-url resolution precedence in the LLM agent adapters.

A multi-model ablation is only meaningful if each row actually runs the model its config names.
Env-first resolution would run one model everywhere while the report labels the rows apart —
a silently wrong result rather than a visible failure, so it is pinned here.
"""

import inspect

import pytest

from aibench.agents.openai_compat import OpenAICompatAgent
from aibench.agents.tool_loop import ToolLoopAgent
from aibench.models import AgentConfig, ModelConfig


def _model(name: str, **kw) -> ModelConfig:
    return ModelConfig.from_dict({"name": name, "model": name, "provider": "openai_compat", **kw})


@pytest.mark.parametrize("adapter", [OpenAICompatAgent, ToolLoopAgent])
def test_model_config_wins_over_env(adapter, monkeypatch):
    monkeypatch.setenv("OPENAI_MODEL", "GLM-5.2")
    src = inspect.getsource(adapter.run)
    assert 'model.model or os.environ.get("OPENAI_MODEL")' in src, (
        "model must resolve config-first; env-first silently collapses a multi-model ablation "
        "onto a single model"
    )


@pytest.mark.parametrize("adapter", [OpenAICompatAgent, ToolLoopAgent])
def test_base_url_is_also_config_first(adapter):
    src = inspect.getsource(adapter.run)
    assert "model.base_url" in src
    assert src.index("model.base_url") < src.index('os.environ.get("OPENAI_BASE_URL")')


def test_env_still_supplies_a_model_when_the_config_leaves_it_blank():
    cfg = _model("")
    assert cfg.model == ""
    resolved = cfg.model or "GLM-5.2"
    assert resolved == "GLM-5.2"


def test_two_model_configs_stay_distinct():
    a, b = _model("GLM-5.1"), _model("GLM-5.2")
    assert a.model != b.model


def test_agent_adapters_accept_distinct_model_configs():
    agent_cfg = AgentConfig.from_dict({"name": "x", "adapter": "openai_compat", "version": "1"})
    a = OpenAICompatAgent(agent_cfg, _model("GLM-5.1"))
    b = OpenAICompatAgent(agent_cfg, _model("GLM-5.2"))
    assert a.model_config.model == "GLM-5.1"
    assert b.model_config.model == "GLM-5.2"
