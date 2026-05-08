"""Typed contracts for the production Codex MLE harness."""

from __future__ import annotations

import json
import re
import uuid
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator


def utc_now() -> datetime:
    """Return a timezone-aware UTC timestamp."""

    return datetime.now(UTC)


def new_id(prefix: str) -> str:
    """Create a stable short identifier with a readable prefix."""

    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def slugify(value: str, *, fallback: str = "item") -> str:
    """Convert a string into a branch/path-safe slug."""

    slug = re.sub(r"[^A-Za-z0-9._/-]+", "-", value.strip()).strip("-")
    slug = slug.replace("//", "/")
    return slug or fallback


class HarnessModel(BaseModel):
    """Base model with JSON-friendly defaults."""

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    def to_json_text(self, *, indent: int = 2) -> str:
        return json.dumps(self.model_dump(mode="json"), indent=indent)

    @classmethod
    def from_json_text(cls, text: str):
        return cls.model_validate_json(text)


class DataMount(HarnessModel):
    """Read-only data made available to attempts."""

    source: Path
    target: str
    read_only: bool = True

    @field_validator("source", mode="before")
    @classmethod
    def _path(cls, value: Any) -> str:
        return str(value)

    @field_serializer("source")
    def _serialize_source(self, value: Path) -> str:
        return str(value)


class ResourceLimits(HarnessModel):
    """Container resource limits for one attempt."""

    memory_limit: str | None = "4g"
    cpu_limit: float | None = 2.0
    gpu_devices: str | list[str] | None = None
    artifact_size_limit_mb: int = 256


class EnvironmentConfig(HarnessModel):
    """Environment variables exposed to evaluator containers."""

    pass_all: bool = False
    allowlist: list[str] = Field(default_factory=list)


class DependencyPolicy(HarnessModel):
    """Candidate-owned dependency installation policy."""

    allow_requirements_txt: bool = True
    requirements_path: str = "requirements.txt"
    install_command: str = (
        "python -m pip install --quiet --root-user-action ignore -r {requirements_path}"
    )
    install_timeout_seconds: int = 300
    failure_exit_code: int = 86


class ImplementationWorkerConfig(HarnessModel):
    """Implementation worker configuration from the task manifest."""

    type: Literal["codex"] = "codex"
    mode: Literal["auto", "exec", "goal"] = "auto"
    fallback_to_exec: bool = True


class StopConditions(HarnessModel):
    """Search termination configuration."""

    max_attempts: int = 3
    max_wall_clock_seconds: int | None = None
    target_metric_value: float | None = None
    plateau_rounds: int = 2


class PlannerConfig(HarnessModel):
    """Planner backend configuration."""

    type: Literal["ml_master", "openai_compatible", "static"] = "static"
    config_path: Path | None = None
    provider: str | None = None
    model: str | None = None
    base_url: str | None = None
    api_key_env: str | None = None
    temperature: float = 0.2
    max_tokens: int = 2048

    @field_validator("config_path", mode="before")
    @classmethod
    def _optional_path(cls, value: Any) -> str | None:
        if value is None:
            return value
        return str(value)

    @field_serializer("config_path")
    def _serialize_config_path(self, value: Path | None) -> str | None:
        return str(value) if value is not None else None


class SchedulerConfig(HarnessModel):
    """Diverse beam search configuration."""

    beam_width: int = 4
    round_size: int = 2
    fresh_draft_fraction: float = 0.25
    novelty_fraction: float = 0.25
    failure_retry_fraction: float = 0.10


class TaskSpec(HarnessModel):
    """Manifest-loaded ML/data-science task definition.

    The manifest owns the primary metric and evaluator. Candidate telemetry in
    ``working/result.json`` may be useful, but the evaluator result is the
    authoritative score.
    """

    task_id: str
    task_name: str | None = None
    description_path: Path
    workspace_path: Path | None = None
    data_mounts: list[DataMount] = Field(default_factory=list)
    support_files: list[Path] = Field(default_factory=list)
    required_outputs: list[str] = Field(default_factory=list)
    entrypoint: str = "run.py"
    evaluator_command: str
    evaluator_result_path: str = "evaluator_result.json"
    primary_metric_name: str
    higher_is_better: bool
    docker_image: str = "python:3.12-slim"
    setup_command: str | None = None
    environment: EnvironmentConfig = Field(default_factory=EnvironmentConfig)
    dependency_policy: DependencyPolicy = Field(default_factory=DependencyPolicy)
    implementation_worker: ImplementationWorkerConfig = Field(default_factory=ImplementationWorkerConfig)
    attempt_timeout_seconds: int = 1800
    evaluator_timeout_seconds: int = 600
    resources: ResourceLimits = Field(default_factory=ResourceLimits)
    stop_conditions: StopConditions = Field(default_factory=StopConditions)
    scheduler: SchedulerConfig = Field(default_factory=SchedulerConfig)
    planner: PlannerConfig = Field(default_factory=PlannerConfig)
    created_at: datetime = Field(default_factory=utc_now)
    manifest_path: Path | None = None

    @field_validator("description_path", "workspace_path", "manifest_path", mode="before")
    @classmethod
    def _paths(cls, value: Any) -> str | None:
        if value is None:
            return value
        return str(value)

    @field_serializer("description_path", "workspace_path", "manifest_path")
    def _serialize_path(self, value: Path | None) -> str | None:
        return str(value) if value is not None else None

    @field_validator("support_files", mode="before")
    @classmethod
    def _support_paths(cls, value: Any) -> list[str]:
        if value is None:
            return []
        return [str(item) for item in value]

    @field_serializer("support_files")
    def _serialize_support_files(self, value: list[Path]) -> list[str]:
        return [str(path) for path in value]

    @field_serializer("created_at")
    def _serialize_created_at(self, value: datetime) -> str:
        return value.isoformat()

    @classmethod
    def from_manifest(cls, manifest_path: Path) -> "TaskSpec":
        """Load a YAML or JSON task manifest and resolve relative paths."""

        manifest_path = manifest_path.resolve()
        raw = manifest_path.read_text(encoding="utf-8")
        if manifest_path.suffix.lower() in {".yaml", ".yml"}:
            try:
                import yaml
            except ImportError as exc:  # pragma: no cover - dependency exists in repo
                raise RuntimeError("PyYAML is required for YAML task manifests") from exc
            data = yaml.safe_load(raw) or {}
        else:
            data = json.loads(raw)

        base = manifest_path.parent
        data["manifest_path"] = manifest_path

        def resolve_path(value: str | Path | None) -> Path | None:
            if value is None:
                return None
            path = value if isinstance(value, Path) else Path(value)
            return path if path.is_absolute() else (base / path).resolve()

        data["description_path"] = resolve_path(data.get("description_path"))
        data["workspace_path"] = resolve_path(data.get("workspace_path")) if data.get("workspace_path") else None
        if data.get("planner", {}).get("config_path"):
            data["planner"]["config_path"] = resolve_path(data["planner"]["config_path"])
        data["support_files"] = [
            resolve_path(path) for path in data.get("support_files", []) or []
        ]

        mounts = []
        for mount in data.get("data_mounts", []) or []:
            item = dict(mount)
            item["source"] = resolve_path(item["source"])
            mounts.append(item)
        data["data_mounts"] = mounts

        return cls.model_validate(data)

    def description_text(self) -> str:
        return self.description_path.read_text(encoding="utf-8")


class WorkOrder(HarnessModel):
    """Planner-generated implementation request for Codex."""

    work_order_id: str = Field(default_factory=lambda: new_id("wo"))
    task_id: str
    parent_attempt_id: str | None = None
    parent_branch: str | None = None
    operator: Literal[
        "draft",
        "fresh_draft",
        "improve",
        "debug",
        "breakthrough_expand",
        "ablation",
        "refactor_for_reliability",
    ] = "draft"
    objective: str
    hypothesis: str | None = None
    constraints: list[str] = Field(default_factory=list)
    allowed_actions: list[str] = Field(default_factory=list)
    required_outputs: list[str] = Field(default_factory=list)
    entrypoint: str = "run.py"
    strategy_tags: list[str] = Field(default_factory=list)
    attempt_index: int = 0
    timeout_seconds: int = 1800
    created_at: datetime = Field(default_factory=utc_now)

    @field_serializer("created_at")
    def _serialize_created_at(self, value: datetime) -> str:
        return value.isoformat()


class ImplementationStatus(str, Enum):
    SUCCESS = "success"
    FAILED = "failed"
    TIMEOUT = "timeout"


class AttemptStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    IMPLEMENTATION_COMPLETED = "implementation_completed"
    EVALUATION_RUNNING = "evaluation_running"
    EVALUATION_COMPLETED = "evaluation_completed"
    SUCCESS = "success"
    FAILED = "failed"
    INVALID = "invalid"
    TIMEOUT = "timeout"
    INTERRUPTED = "interrupted"


class EvaluationStatus(str, Enum):
    SUCCESS = "success"
    FAILED = "failed"
    INVALID = "invalid"
    TIMEOUT = "timeout"
    SKIPPED = "skipped"


class FailureClass(str, Enum):
    """Stable failure classes used by runner state and planner memory."""

    CODEX_CLI_MISSING = "codex_cli_missing"
    CODEX_CLI_ERROR = "codex_cli_error"
    CODEX_TIMEOUT = "codex_timeout"
    DEPENDENCY_INSTALL_FAILED = "dependency_install_failed"
    NO_CODE_WRITTEN = "no_code_written"
    MISSING_REQUIRED_OUTPUT = "missing_required_output"
    ENTRYPOINT_FAILED = "entrypoint_failed"
    EVALUATOR_FAILED = "evaluator_failed"
    EVALUATOR_TIMEOUT = "evaluator_timeout"
    INVALID_EVALUATOR_RESULT = "invalid_evaluator_result"
    MISSING_EVALUATOR_RESULT = "missing_evaluator_result"
    MISSING_METRIC_VALUE = "missing_metric_value"
    DOCKER_ERROR = "docker_error"
    GIT_ERROR = "git_error"
    RESOURCE_LIMIT = "resource_limit"
    PLANNER_INVALID_OUTPUT = "planner_invalid_output"
    INTERRUPTED = "interrupted"
    MISSING_EVALUATION = "missing_evaluation"
    UNKNOWN = "unknown"


class ImplementationResult(HarnessModel):
    """Result of invoking the implementation worker."""

    work_order_id: str
    status: ImplementationStatus
    exit_code: int | None = None
    stdout: str = ""
    stderr: str = ""
    changed_files: list[str] = Field(default_factory=list)
    patch: str | None = None
    branch_name: str | None = None
    runtime_seconds: float | None = None
    failure_class: str | None = None
    notes: str | None = None


class EvaluatorResult(HarnessModel):
    """Authoritative evaluator output."""

    attempt_id: str
    status: EvaluationStatus
    metric_name: str
    metric_value: float | None = None
    higher_is_better: bool
    valid: bool = True
    diagnostics: dict[str, Any] = Field(default_factory=dict)
    extra_metrics: dict[str, float] = Field(default_factory=dict)
    stdout: str = ""
    stderr: str = ""
    runtime_seconds: float | None = None
    failure_class: str | None = None
    created_at: datetime = Field(default_factory=utc_now)

    @field_serializer("created_at")
    def _serialize_created_at(self, value: datetime) -> str:
        return value.isoformat()


class ContainerMetadata(HarnessModel):
    """Docker metadata for an attempt/evaluator container."""

    image: str
    container_name: str | None = None
    container_id: str | None = None
    command: str
    working_dir: str = "/workspace"
    volumes: dict[str, str] = Field(default_factory=dict)
    environment_count: int = 0
    memory_limit: str | None = None
    cpu_limit: float | None = None
    gpu_devices: str | list[str] | None = None


class ExperimentResult(HarnessModel):
    """Immutable attempt record stored in SQLite."""

    attempt_id: str = Field(default_factory=lambda: new_id("attempt"))
    task_id: str
    work_order_id: str
    parent_attempt_id: str | None = None
    branch_name: str
    parent_branch: str | None = None
    parent_commit_sha: str | None = None
    commit_sha: str | None = None
    artifact_dir: Path
    workspace_path: Path
    status: AttemptStatus = AttemptStatus.QUEUED
    implementation_status: ImplementationStatus | None = None
    evaluator_status: EvaluationStatus | None = None
    metric_name: str | None = None
    metric_value: float | None = None
    higher_is_better: bool | None = None
    failure_reason: str | None = None
    failure_class: str | None = None
    runtime_seconds: float | None = None
    resource_usage: dict[str, Any] = Field(default_factory=dict)
    container: ContainerMetadata | None = None
    breakthrough: bool = False
    breakthrough_reason: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    completed_at: datetime | None = None

    @field_validator("artifact_dir", "workspace_path", mode="before")
    @classmethod
    def _path(cls, value: Any) -> str:
        return str(value)

    @field_serializer("artifact_dir", "workspace_path")
    def _serialize_paths(self, value: Path) -> str:
        return str(value)

    @field_serializer("created_at", "completed_at")
    def _serialize_dt(self, value: datetime | None) -> str | None:
        return value.isoformat() if value is not None else None


class SearchNode(HarnessModel):
    """Tree-search node linked to an attempt."""

    node_id: str = Field(default_factory=lambda: new_id("node"))
    task_id: str
    attempt_id: str | None = None
    work_order_id: str | None = None
    parent_node_id: str | None = None
    parent_attempt_id: str | None = None
    depth: int = 0
    objective: str
    hypothesis: str | None = None
    novelty_key: str | None = None
    score: float | None = None
    status: AttemptStatus = AttemptStatus.QUEUED
    created_at: datetime = Field(default_factory=utc_now)

    @field_serializer("created_at")
    def _serialize_created_at(self, value: datetime) -> str:
        return value.isoformat()


class PlannerIdea(HarnessModel):
    """A planner idea that can become a WorkOrder."""

    objective: str
    hypothesis: str | None = None
    rationale: str | None = None
    operator: Literal[
        "draft",
        "fresh_draft",
        "improve",
        "debug",
        "breakthrough_expand",
        "ablation",
        "refactor_for_reliability",
    ] = "improve"
    parent_attempt_id: str | None = None
    novelty_key: str | None = None
    strategy_tags: list[str] = Field(default_factory=list)


class AttemptSummary(HarnessModel):
    """Compact structured memory produced after every attempt."""

    attempt_id: str
    task_id: str
    work_order_id: str
    operator: str | None = None
    parent_attempt_id: str | None = None
    status: str
    metric_name: str | None = None
    metric_value: float | None = None
    higher_is_better: bool | None = None
    failure_class: str | None = None
    root_cause: str | None = None
    candidate_strategy: str | None = None
    implementation_summary: str | None = None
    validation_claim: dict[str, Any] = Field(default_factory=dict)
    evaluator_outcome: dict[str, Any] = Field(default_factory=dict)
    dependencies: list[str] = Field(default_factory=list)
    dependency_files: list[str] = Field(default_factory=list)
    changed_files: list[str] = Field(default_factory=list)
    runtime_seconds: float | None = None
    branch_name: str | None = None
    commit_sha: str | None = None
    breakthrough: bool = False
    breakthrough_reason: str | None = None
    lessons: list[str] = Field(default_factory=list)
    next_recommended_actions: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)

    @field_serializer("created_at")
    def _serialize_summary_created_at(self, value: datetime) -> str:
        return value.isoformat()


class PlannerValidationReport(HarnessModel):
    """Record of planner output validation and deterministic repair."""

    round_index: int
    planner_name: str
    raw_text: str
    repaired_json: dict[str, Any] = Field(default_factory=dict)
    valid: bool = True
    repaired: bool = False
    errors: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)

    @field_serializer("created_at")
    def _serialize_created_at(self, value: datetime) -> str:
        return value.isoformat()


class PromotionRecord(HarnessModel):
    """Planner memory/wisdom promotion output for a scheduler round."""

    promotion_id: str = Field(default_factory=lambda: new_id("promotion"))
    task_id: str
    round_index: int
    planner_name: str
    content: str
    source_attempt_ids: list[str] = Field(default_factory=list)
    artifact_path: Path | None = None
    created_at: datetime = Field(default_factory=utc_now)

    @field_validator("artifact_path", mode="before")
    @classmethod
    def _optional_path(cls, value: Any) -> str | None:
        if value is None:
            return None
        return str(value)

    @field_serializer("artifact_path")
    def _serialize_artifact_path(self, value: Path | None) -> str | None:
        return str(value) if value is not None else None

    @field_serializer("created_at")
    def _serialize_promotion_created_at(self, value: datetime) -> str:
        return value.isoformat()


class AdoptionRecord(HarnessModel):
    """Branch promotion/adoption event for the current best attempt."""

    adoption_id: str = Field(default_factory=lambda: new_id("adoption"))
    task_id: str
    attempt_id: str
    branch_name: str
    adopted_branch: str
    commit_sha: str | None = None
    metric_name: str | None = None
    metric_value: float | None = None
    higher_is_better: bool | None = None
    notes: str | None = None
    created_at: datetime = Field(default_factory=utc_now)

    @field_serializer("created_at")
    def _serialize_adoption_created_at(self, value: datetime) -> str:
        return value.isoformat()
