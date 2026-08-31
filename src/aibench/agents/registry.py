from __future__ import annotations

from aibench.agents.bare_model import BareModelAgent
from aibench.agents.base import AgentAdapter
from aibench.agents.mock import MockAgent
from aibench.agents.openai_compat import OpenAICompatAgent
from aibench.agents.opencode import OpenCodeAgent
from aibench.agents.pi import PiAgent
from aibench.agents.shell_agent import ShellAgent
from aibench.agents.tool_loop import ToolLoopAgent
from aibench.models import AgentConfig, ModelConfig

_REGISTRY: dict[str, type[AgentAdapter]] = {
    "mock": MockAgent,
    "bare_model": BareModelAgent,
    "openai_compat": OpenAICompatAgent,
    "opencode": OpenCodeAgent,
    "pi": PiAgent,
    "tool_loop": ToolLoopAgent,
    "shell": ShellAgent,
}


def create_agent(agent_config: AgentConfig, model_config: ModelConfig) -> AgentAdapter:
    key = agent_config.adapter
    if key not in _REGISTRY:
        known = ", ".join(sorted(_REGISTRY))
        raise KeyError(f"Unknown agent adapter '{key}'. Known: {known}")
    return _REGISTRY[key](agent_config, model_config)


def register_adapter(name: str, cls: type[AgentAdapter]) -> None:
    _REGISTRY[name] = cls
