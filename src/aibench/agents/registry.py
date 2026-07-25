from __future__ import annotations

from aibench.agents.base import AgentAdapter
from aibench.agents.mock import MockAgent
from aibench.agents.openai_compat import OpenAICompatAgent
from aibench.models import AgentConfig, ModelConfig

_REGISTRY: dict[str, type[AgentAdapter]] = {
    "mock": MockAgent,
    "openai_compat": OpenAICompatAgent,
}


def create_agent(agent_config: AgentConfig, model_config: ModelConfig) -> AgentAdapter:
    key = agent_config.adapter
    if key not in _REGISTRY:
        known = ", ".join(sorted(_REGISTRY))
        raise KeyError(f"Unknown agent adapter '{key}'. Known: {known}")
    return _REGISTRY[key](agent_config, model_config)


def register_adapter(name: str, cls: type[AgentAdapter]) -> None:
    _REGISTRY[name] = cls
