from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class FileBlob:
    path: str
    content: str
    role: str = "impl"  # impl | test | distractor | spec

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> FileBlob:
        return cls(path=d["path"], content=d["content"], role=d.get("role") or "impl")


@dataclass
class GraderSpec:
    mode: str
    command: str | None = None
    gold_files: list[FileBlob] = field(default_factory=list)
    match: str = "normalized"
    key_lines: list[str] = field(default_factory=list)
    judge_rubric: str | None = None
    judge_threshold: float | None = 0.7
    hidden_tests: list[FileBlob] = field(default_factory=list)
    protected_paths: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> GraderSpec:
        gold = [FileBlob.from_dict(x) for x in d.get("gold_files") or []]
        hidden = [FileBlob.from_dict(x) for x in d.get("hidden_tests") or []]
        return cls(
            mode=d["mode"],
            command=d.get("command"),
            gold_files=gold,
            match=d.get("match") or "normalized",
            key_lines=list(d.get("key_lines") or []),
            judge_rubric=d.get("judge_rubric"),
            judge_threshold=d.get("judge_threshold", 0.7),
            hidden_tests=hidden,
            protected_paths=list(d.get("protected_paths") or []),
        )


@dataclass
class Case:
    case_id: str
    schema_version: str
    task_type: str
    language: str
    prompt: str
    files: list[FileBlob]
    grader: GraderSpec
    notes: str | None = None
    workspace: Any = None  # WorkspaceSpec; set in from_dict
    metadata: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Case:
        from aibench.workspace import WorkspaceSpec

        ctx = d["context"]
        files = [FileBlob.from_dict(x) for x in ctx.get("files") or []]
        ws = WorkspaceSpec.from_dict(ctx.get("workspace"))
        return cls(
            case_id=d["case_id"],
            schema_version=d.get("schema_version", "0.1"),
            task_type=d["task_type"],
            language=d["language"],
            prompt=d["prompt"],
            files=files,
            grader=GraderSpec.from_dict(d["grader"]),
            notes=ctx.get("notes"),
            workspace=ws,
            metadata=dict(d.get("metadata") or {}),
            raw=d,
        )

    @property
    def tier(self) -> str | None:
        t = self.metadata.get("tier")
        return str(t) if t else None


@dataclass
class ModelConfig:
    name: str
    provider: str
    model: str
    base_url: str | None = None
    api_key_env: str | None = None
    temperature: float = 0.0
    max_tokens: int = 4096
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ModelConfig:
        return cls(
            name=d["name"],
            provider=d.get("provider", "openai_compat"),
            model=d["model"],
            base_url=d.get("base_url"),
            api_key_env=d.get("api_key_env"),
            temperature=float(d.get("temperature", 0)),
            max_tokens=int(d.get("max_tokens", 4096)),
            extra=dict(d.get("extra") or {}),
        )


@dataclass
class AgentConfig:
    name: str
    version: str
    adapter: str
    options: dict[str, Any] = field(default_factory=dict)
    description: str = ""
    #: Capability axes this scaffold can actually exercise (see aibench.tiers.AXES). An agent
    #: that hands the model every file cannot exhibit or lack retrieval, so scoring it on a
    #: retrieval case measures something else. Empty means "unknown", treated as unrestricted
    #: for backward compatibility.
    capability_axes: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> AgentConfig:
        return cls(
            name=d["name"],
            version=str(d.get("version", "0")),
            adapter=d["adapter"],
            options=dict(d.get("options") or {}),
            description=d.get("description") or "",
            capability_axes=tuple(d.get("capability_axes") or ()),
        )


@dataclass
class RunConfig:
    experiment_name: str
    algorithm_name: str
    algorithm_version: str
    budget_axis: str
    budget_value: str
    branches: int
    max_attempts: int
    max_steps: int
    max_wall_time_s: float
    selection_strategy: str
    case_set: str
    benchmark_name: str
    grouping: str
    agent_config_path: str
    model_config_path: str
    case_workers: int = 1

    @classmethod
    def from_dict(cls, d: dict[str, Any], base_dir: Path | None = None) -> RunConfig:
        return cls(
            experiment_name=d.get("experiment_name", "unnamed"),
            algorithm_name=d.get("algorithm_name", "Baseline"),
            algorithm_version=d.get("algorithm_version", "v0"),
            budget_axis=d.get("budget_axis", "steps"),
            budget_value=str(d.get("budget_value", "")),
            branches=int(d.get("branches", 1)),
            max_attempts=int(d.get("max_attempts", 1)),
            max_steps=int(d.get("max_steps", 40)),
            max_wall_time_s=float(d.get("max_wall_time_s", 300)),
            selection_strategy=d.get("selection_strategy", "first-submit"),
            case_set=d.get("case_set", "auto-v0"),
            benchmark_name=d.get("benchmark_name", "AI-Coding-Assist"),
            grouping=d.get("grouping", "task_type"),
            agent_config_path=d.get("agent_config", "configs/agents/mock.yaml"),
            model_config_path=d.get("model_config", "configs/models/mock-model.yaml"),
            case_workers=int(d.get("case_workers", 1)),
        )


@dataclass
class StepRecord:
    step_index: int
    action: str
    tool: str | None = None
    duration_ms: float | None = None
    detail: str | None = None


@dataclass
class UsageRecord:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    model_calls: int = 0


@dataclass
class AgentRunResult:
    status: str  # completed | failed | timeout | infra_error
    artifacts: dict[str, Any] = field(default_factory=dict)
    usage: UsageRecord = field(default_factory=UsageRecord)
    steps: list[StepRecord] = field(default_factory=list)
    wall_time_s: float = 0.0
    error_message: str | None = None
    empty_patch: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "artifacts": self.artifacts,
            "usage": asdict(self.usage),
            "steps": [asdict(s) for s in self.steps],
            "wall_time_s": self.wall_time_s,
            "error_message": self.error_message,
            "empty_patch": self.empty_patch,
        }


@dataclass
class GradeResult:
    passed: bool
    mode: str
    score: float | None = None
    detail: str = ""
    infra_error: bool = False
    reward_hack: bool = False
    test_pass_ratio: float | None = None
    #: The suite never ran (import error, syntax error, nothing collected) — the workspace is
    #: broken rather than the submission wrong. Distinct from ``infra_error``, which is the
    #: harness failing; this is the case itself failing to stand up.
    collection_error: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
