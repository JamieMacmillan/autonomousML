"""Evaluator command runner and result parsing."""

from __future__ import annotations

import json
from pathlib import Path

from codex_mle_harness.core.models import (
    EvaluationStatus,
    EvaluatorResult,
    FailureClass,
    TaskSpec,
)
from codex_mle_harness.execution.docker_runner import DockerRunner


class Evaluator:
    """Runs the task-owned evaluator command in Docker."""

    def __init__(self, docker_runner: DockerRunner | None = None):
        self.docker_runner = docker_runner or DockerRunner()

    def run(self, *, attempt_id: str, task: TaskSpec, workspace: Path) -> tuple[EvaluatorResult, object]:
        docker_result = self.docker_runner.run(
            task=task,
            workspace=workspace,
            command=task.evaluator_command,
            timeout_seconds=task.evaluator_timeout_seconds,
            container_name=f"codex-mle-eval-{attempt_id[:12]}",
        )
        result_path = workspace / task.evaluator_result_path
        dependency_diagnostics = _dependency_diagnostics(workspace)
        if docker_result.exit_code == task.dependency_policy.failure_exit_code:
            return (
                EvaluatorResult(
                    attempt_id=attempt_id,
                    status=EvaluationStatus.FAILED,
                    metric_name=task.primary_metric_name,
                    higher_is_better=task.higher_is_better,
                    valid=False,
                    stdout=docker_result.stdout,
                    stderr=docker_result.stderr,
                    runtime_seconds=docker_result.runtime_seconds,
                    failure_class=FailureClass.DEPENDENCY_INSTALL_FAILED.value,
                    diagnostics=dependency_diagnostics,
                ),
                docker_result,
            )
        if docker_result.timed_out:
            return (
                EvaluatorResult(
                    attempt_id=attempt_id,
                    status=EvaluationStatus.TIMEOUT,
                    metric_name=task.primary_metric_name,
                    higher_is_better=task.higher_is_better,
                    valid=False,
                    stdout=docker_result.stdout,
                    stderr=docker_result.stderr,
                    runtime_seconds=docker_result.runtime_seconds,
                    failure_class=FailureClass.EVALUATOR_TIMEOUT.value,
                    diagnostics=dependency_diagnostics,
                ),
                docker_result,
            )
        if docker_result.exit_code != 0:
            return (
                EvaluatorResult(
                    attempt_id=attempt_id,
                    status=EvaluationStatus.FAILED,
                    metric_name=task.primary_metric_name,
                    higher_is_better=task.higher_is_better,
                    valid=False,
                    stdout=docker_result.stdout,
                    stderr=docker_result.stderr,
                    runtime_seconds=docker_result.runtime_seconds,
                    failure_class=FailureClass.EVALUATOR_FAILED.value,
                    diagnostics=dependency_diagnostics,
                ),
                docker_result,
            )
        if not result_path.exists():
            return (
                EvaluatorResult(
                    attempt_id=attempt_id,
                    status=EvaluationStatus.INVALID,
                    metric_name=task.primary_metric_name,
                    higher_is_better=task.higher_is_better,
                    valid=False,
                    stdout=docker_result.stdout,
                    stderr=docker_result.stderr,
                    runtime_seconds=docker_result.runtime_seconds,
                    failure_class=FailureClass.MISSING_EVALUATOR_RESULT.value,
                    diagnostics={**dependency_diagnostics, "missing_path": str(result_path)},
                ),
                docker_result,
            )
        try:
            data = json.loads(result_path.read_text(encoding="utf-8"))
            metric_name = data.get("metric_name", task.primary_metric_name)
            higher_is_better = data.get("higher_is_better", task.higher_is_better)
            if metric_name != task.primary_metric_name or higher_is_better != task.higher_is_better:
                raise ValueError(
                    "Evaluator result metric name/direction does not match task manifest"
                )
            result = EvaluatorResult(
                attempt_id=attempt_id,
                status=EvaluationStatus.SUCCESS if data.get("valid", True) else EvaluationStatus.INVALID,
                metric_name=metric_name,
                metric_value=data.get("metric_value"),
                higher_is_better=higher_is_better,
                valid=data.get("valid", True),
                diagnostics={**dependency_diagnostics, **data.get("diagnostics", {})},
                extra_metrics=data.get("extra_metrics", {}),
                stdout=docker_result.stdout,
                stderr=docker_result.stderr,
                runtime_seconds=docker_result.runtime_seconds,
            )
            if result.metric_value is None:
                result.status = EvaluationStatus.INVALID
                result.valid = False
                result.failure_class = FailureClass.MISSING_METRIC_VALUE.value
            elif result.status == EvaluationStatus.INVALID and result.diagnostics.get("error") == "entrypoint_failed":
                result.failure_class = FailureClass.ENTRYPOINT_FAILED.value
            elif result.status == EvaluationStatus.INVALID and result.diagnostics.get("error") == "entrypoint_timeout":
                result.failure_class = FailureClass.EVALUATOR_TIMEOUT.value
            return result, docker_result
        except Exception as exc:
            return (
                EvaluatorResult(
                    attempt_id=attempt_id,
                    status=EvaluationStatus.INVALID,
                    metric_name=task.primary_metric_name,
                    higher_is_better=task.higher_is_better,
                    valid=False,
                    stdout=docker_result.stdout,
                    stderr=docker_result.stderr,
                    runtime_seconds=docker_result.runtime_seconds,
                    failure_class=FailureClass.INVALID_EVALUATOR_RESULT.value,
                    diagnostics={**dependency_diagnostics, "error": str(exc), "path": str(result_path)},
                ),
                docker_result,
            )


def _dependency_diagnostics(workspace: Path) -> dict[str, object]:
    dep_dir = Path(workspace) / ".codex_mle_harness"
    exit_path = dep_dir / "dependency_install_exit_code.txt"
    stdout_path = dep_dir / "dependency_install_stdout.txt"
    stderr_path = dep_dir / "dependency_install_stderr.txt"
    if not exit_path.exists() and not stdout_path.exists() and not stderr_path.exists():
        return {}

    def read_text(path: Path) -> str:
        return path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""

    exit_text = read_text(exit_path).strip()
    return {
        "dependency_install_exit_code": int(exit_text) if exit_text.isdigit() else None,
        "dependency_install_stdout": read_text(stdout_path),
        "dependency_install_stderr": read_text(stderr_path),
    }
