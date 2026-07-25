from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from aibench.models import AgentConfig, AgentRunResult, Case, ModelConfig


class AgentAdapter(ABC):
    """Pluggable agent interface."""

    def __init__(self, agent_config: AgentConfig, model_config: ModelConfig) -> None:
        self.agent_config = agent_config
        self.model_config = model_config

    @abstractmethod
    def run(
        self,
        case: Case,
        workspace: Path,
        *,
        max_steps: int,
        max_wall_time_s: float,
    ) -> AgentRunResult:
        raise NotImplementedError
