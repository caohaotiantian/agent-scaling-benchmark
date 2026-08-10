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


def request_timeout_s(max_wall_time_s: float, *, default: float = 240.0) -> float:
    """Per-request HTTP timeout for an agent call.

    Capped at 120s, a slow gateway produced 5-9% infra errors in a calibration sweep — noise
    that is excluded from the rates but still costs a full retry of the case. The cap must
    never exceed the case's own wall-clock budget, which is the real deadline.
    Override with AIBENCH_REQUEST_TIMEOUT.
    """
    import os

    try:
        configured = float(os.environ.get("AIBENCH_REQUEST_TIMEOUT", default))
    except ValueError:
        configured = default
    return min(max(30.0, configured), max_wall_time_s)
