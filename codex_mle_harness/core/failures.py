"""Failure taxonomy helpers for attempt recording and planner memory."""

from __future__ import annotations

from pathlib import Path

from codex_mle_harness.core.models import (
    EvaluationStatus,
    EvaluatorResult,
    FailureClass,
    ImplementationResult,
    ImplementationStatus,
    TaskSpec,
)


TERMINAL_FAILURE_CLASSES = {
    FailureClass.CODEX_CLI_MISSING.value,
    FailureClass.CODEX_CLI_ERROR.value,
    FailureClass.CODEX_TIMEOUT.value,
    FailureClass.DEPENDENCY_INSTALL_FAILED.value,
    FailureClass.NO_CODE_WRITTEN.value,
    FailureClass.MISSING_REQUIRED_OUTPUT.value,
    FailureClass.EVALUATOR_FAILED.value,
    FailureClass.EVALUATOR_TIMEOUT.value,
    FailureClass.INVALID_EVALUATOR_RESULT.value,
    FailureClass.MISSING_EVALUATOR_RESULT.value,
    FailureClass.MISSING_METRIC_VALUE.value,
    FailureClass.DOCKER_ERROR.value,
    FailureClass.RESOURCE_LIMIT.value,
    FailureClass.INTERRUPTED.value,
}


def classify_implementation(result: ImplementationResult) -> str | None:
    """Normalize implementation worker failures into stable classes."""

    if result.status == ImplementationStatus.SUCCESS:
        return result.failure_class
    if result.status == ImplementationStatus.TIMEOUT:
        return FailureClass.CODEX_TIMEOUT.value
    if result.failure_class in {FailureClass.CODEX_CLI_MISSING.value, FailureClass.CODEX_CLI_ERROR.value}:
        return result.failure_class
    stderr = (result.stderr or "").lower()
    if "not installed" in stderr or "not on path" in stderr:
        return FailureClass.CODEX_CLI_MISSING.value
    return FailureClass.CODEX_CLI_ERROR.value


def missing_required_outputs(task: TaskSpec, workspace: Path) -> list[str]:
    """Return required outputs absent from the attempt workspace."""

    missing: list[str] = []
    for rel in task.required_outputs:
        if not (Path(workspace) / rel).exists():
            missing.append(rel)
    return missing


def classify_evaluator(result: EvaluatorResult) -> str | None:
    """Normalize evaluator failures into stable classes."""

    if result.status == EvaluationStatus.SUCCESS and result.valid:
        return result.failure_class
    if result.status == EvaluationStatus.TIMEOUT:
        return FailureClass.EVALUATOR_TIMEOUT.value
    if result.failure_class:
        return result.failure_class
    if result.status == EvaluationStatus.INVALID:
        return FailureClass.INVALID_EVALUATOR_RESULT.value
    if result.status == EvaluationStatus.FAILED:
        return FailureClass.EVALUATOR_FAILED.value
    return FailureClass.UNKNOWN.value
