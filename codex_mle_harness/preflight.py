"""Preflight validation for task manifests and local runtime readiness."""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
from collections import defaultdict
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_serializer

from codex_mle_harness.core.models import TaskSpec
from codex_mle_harness.utils.task_files import (
    is_reserved_workspace_path,
    support_file_destinations,
    task_root,
    workspace_relative_path,
)

Severity = Literal["error", "warning", "info"]


class PreflightCheck(BaseModel):
    """One preflight finding."""

    severity: Severity
    code: str
    message: str
    path: Path | None = None

    @field_serializer("path")
    def _serialize_path(self, value: Path | None) -> str | None:
        return str(value) if value is not None else None


class PreflightReport(BaseModel):
    """Structured preflight result for CLI and tests."""

    task_id: str
    manifest_path: Path | None
    ok: bool
    checks: list[PreflightCheck] = Field(default_factory=list)

    @field_serializer("manifest_path")
    def _serialize_manifest_path(self, value: Path | None) -> str | None:
        return str(value) if value is not None else None

    @property
    def errors(self) -> list[PreflightCheck]:
        return [check for check in self.checks if check.severity == "error"]

    @property
    def warnings(self) -> list[PreflightCheck]:
        return [check for check in self.checks if check.severity == "warning"]

    def to_text(self) -> str:
        lines = [
            f"Task: {self.task_id}",
            f"Manifest: {self.manifest_path or 'not recorded'}",
            f"Status: {'OK' if self.ok else 'FAILED'}",
            "",
        ]
        for check in self.checks:
            location = f" [{check.path}]" if check.path is not None else ""
            lines.append(f"{check.severity.upper()} {check.code}: {check.message}{location}")
        if not self.checks:
            lines.append("No preflight problems found.")
        return "\n".join(lines)


def validate_task_preflight(
    task: TaskSpec,
    *,
    check_runtime: bool = True,
    require_codex: bool = True,
    require_docker: bool = True,
) -> PreflightReport:
    """Validate a task before spending an unattended run budget."""

    checks: list[PreflightCheck] = []

    def add(severity: Severity, code: str, message: str, path: Path | None = None) -> None:
        checks.append(PreflightCheck(severity=severity, code=code, message=message, path=path))

    root = task_root(task)
    if task.manifest_path is None:
        add("warning", "manifest_path_missing", "Task was not loaded from a manifest path.")
    elif not task.manifest_path.exists():
        add("error", "manifest_missing", "Manifest path does not exist.", task.manifest_path)

    if not task.description_path.exists():
        add("error", "description_missing", "Task description file does not exist.", task.description_path)
    elif not task.description_path.is_file():
        add("error", "description_not_file", "Task description path is not a file.", task.description_path)
    elif not task.description_path.read_text(encoding="utf-8", errors="replace").strip():
        add("warning", "description_empty", "Task description is empty.", task.description_path)

    _validate_workspace_path(
        value=task.evaluator_result_path,
        code_prefix="evaluator_result_path",
        label="Evaluator result path",
        add=add,
    )
    for output in task.required_outputs:
        _validate_workspace_path(
            value=output,
            code_prefix="required_output",
            label="Required output path",
            add=add,
        )

    if not task.evaluator_command.strip():
        add("error", "evaluator_command_empty", "Evaluator command is empty.")
    elif root is not None:
        for script in _referenced_task_scripts(task.evaluator_command, root):
            if not script.exists():
                add(
                    "error",
                    "evaluator_script_missing",
                    "Evaluator command references a missing task script.",
                    script,
                )

    _validate_data_mounts(task, add=add)
    _validate_support_files(task, add=add)
    _validate_stop_conditions(task, add=add)
    _validate_planner(task, check_runtime=check_runtime, add=add)

    if check_runtime:
        if require_docker:
            _validate_docker(add=add)
        if require_codex and task.implementation_worker.type == "codex":
            _validate_codex(add=add)

    ok = not any(check.severity == "error" for check in checks)
    return PreflightReport(
        task_id=task.task_id,
        manifest_path=task.manifest_path,
        ok=ok,
        checks=checks,
    )


def _validate_workspace_path(
    *,
    value: str,
    code_prefix: str,
    label: str,
    add,
) -> None:
    try:
        workspace_relative_path(value)
    except ValueError as exc:
        add("error", f"{code_prefix}_invalid", f"{label} is invalid: {exc}")


def _validate_data_mounts(task: TaskSpec, *, add) -> None:
    seen_targets: set[str] = set()
    for mount in task.data_mounts:
        if not mount.source.exists():
            add("error", "data_mount_missing", "Data mount source does not exist.", mount.source)
        if mount.target in seen_targets:
            add("error", "data_mount_duplicate_target", "Duplicate data mount target.", Path(mount.target))
        seen_targets.add(mount.target)
        try:
            workspace_relative_path(mount.target)
        except ValueError as exc:
            add(
                "error",
                "data_mount_target_invalid",
                f"Data mount target should be workspace-relative for candidate visibility: {exc}",
            )


def _validate_support_files(task: TaskSpec, *, add) -> None:
    destinations = support_file_destinations(task)
    by_dest: dict[Path, list[Path]] = defaultdict(list)
    root = task_root(task)
    for source, dest in destinations.items():
        if not source.exists():
            add("error", "support_file_missing", "Support file does not exist.", source)
        elif not source.is_file():
            add("error", "support_file_not_file", "Support file must be a regular file.", source)
        if is_reserved_workspace_path(dest):
            add(
                "error",
                "support_file_reserved_destination",
                "Support file destination would overwrite a harness-owned workspace path.",
                dest,
            )
        if root is not None and not _is_relative_to(source, root):
            add(
                "warning",
                "support_file_external",
                "External support file will be copied under support_files/ in each attempt workspace.",
                source,
            )
        by_dest[dest].append(source)
    for dest, sources in by_dest.items():
        if len(sources) > 1:
            add(
                "error",
                "support_file_destination_collision",
                "Multiple support files map to the same workspace destination: "
                + ", ".join(str(source) for source in sources),
                dest,
            )


def _validate_stop_conditions(task: TaskSpec, *, add) -> None:
    stop = task.stop_conditions
    if stop.max_attempts < 1:
        add("error", "max_attempts_invalid", "max_attempts must be at least 1.")
    if stop.max_wall_clock_seconds is not None and stop.max_wall_clock_seconds <= 0:
        add("error", "max_wall_clock_invalid", "max_wall_clock_seconds must be positive when set.")
    if task.attempt_timeout_seconds <= 0:
        add("error", "attempt_timeout_invalid", "attempt_timeout_seconds must be positive.")
    if task.evaluator_timeout_seconds <= 0:
        add("error", "evaluator_timeout_invalid", "evaluator_timeout_seconds must be positive.")
    if stop.max_wall_clock_seconds is not None:
        per_attempt_budget = task.attempt_timeout_seconds + task.evaluator_timeout_seconds
        if per_attempt_budget > stop.max_wall_clock_seconds:
            add(
                "warning",
                "wall_clock_soft_budget",
                "Wall-clock stopping is checked between attempts; one attempt plus evaluation can exceed the global budget.",
            )
    if stop.plateau_rounds < 0:
        add("error", "plateau_rounds_invalid", "plateau_rounds must be zero or positive.")


def _validate_planner(task: TaskSpec, *, check_runtime: bool, add) -> None:
    if task.planner.type == "static":
        return
    env_name = task.planner.api_key_env or "CEREBRAS_API_KEY"
    if check_runtime and not os.environ.get(env_name):
        add("error", "planner_api_key_missing", f"Planner API key environment variable is not set: {env_name}.")
    if not task.planner.model:
        add(
            "warning",
            "planner_model_default",
            "Planner model is not set in the manifest; runtime defaults will be used.",
        )


def _validate_docker(*, add) -> None:
    docker = shutil.which("docker")
    if not docker:
        add("error", "docker_missing", "Docker CLI is not installed or not on PATH.")
        return
    try:
        result = subprocess.run(
            [docker, "version", "--format", "{{.Server.Version}}"],
            text=True,
            capture_output=True,
            timeout=10,
            check=False,
        )
    except Exception as exc:
        add("error", "docker_unavailable", f"Docker runtime check failed: {type(exc).__name__}: {exc}")
        return
    if result.returncode != 0:
        add("error", "docker_unavailable", f"Docker daemon is not available: {(result.stderr or result.stdout).strip()}")


def _validate_codex(*, add) -> None:
    codex = shutil.which("codex")
    if not codex:
        add("error", "codex_cli_missing", "Codex CLI is not installed or not on PATH.")
        return
    try:
        result = subprocess.run(
            [codex, "exec", "--help"],
            text=True,
            capture_output=True,
            timeout=10,
            check=False,
        )
    except Exception as exc:
        add("error", "codex_cli_unavailable", f"Codex CLI runtime check failed: {type(exc).__name__}: {exc}")
        return
    if result.returncode != 0:
        add("error", "codex_cli_unavailable", f"Codex CLI is not callable: {(result.stderr or result.stdout).strip()}")


def _referenced_task_scripts(command: str, root: Path) -> list[Path]:
    try:
        parts = shlex.split(command)
    except ValueError:
        return []
    scripts: list[Path] = []
    for part in parts:
        if part.startswith("/task/") and part.endswith(".py"):
            scripts.append(Path(part.replace("/task/", "", 1)))
    return [(root / script).resolve() for script in scripts]


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True
