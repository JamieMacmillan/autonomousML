"""Structured attempt-memory extraction."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from codex_mle_harness.core.experiment_store import ExperimentStore
from codex_mle_harness.core.models import AttemptStatus, AttemptSummary, ExperimentResult, TaskSpec


def build_attempt_summary(
    *,
    task: TaskSpec,
    attempt: ExperimentResult,
    store: ExperimentStore,
    artifact_dir: Path,
    workspace: Path,
) -> AttemptSummary:
    work_order = store.get_work_order(attempt.work_order_id)
    implementation = _read_json(artifact_dir / "implementation_result.json")
    evaluator = _read_json(artifact_dir / "evaluator_result.json")
    candidate = _read_json(artifact_dir / "candidate_result.json") or _read_json(
        Path(workspace) / "working" / "result.json"
    )
    changed_files = [str(item) for item in implementation.get("changed_files", [])]
    dependency_files = _dependency_files(workspace, task)
    dependencies = _dependencies(workspace, task)
    root_cause = _root_cause(attempt, evaluator, implementation)
    lessons = _lessons(attempt, root_cause, dependencies, candidate)
    next_actions = _next_actions(attempt, root_cause)
    breakthrough, breakthrough_reason = _breakthrough(
        task=task,
        attempt=attempt,
        store=store,
        root_cause=root_cause,
    )
    strategy = _candidate_strategy(candidate, work_order.objective if work_order else None)
    summary = AttemptSummary(
        attempt_id=attempt.attempt_id,
        task_id=attempt.task_id,
        work_order_id=attempt.work_order_id,
        operator=work_order.operator if work_order else None,
        parent_attempt_id=attempt.parent_attempt_id,
        status=attempt.status.value,
        metric_name=attempt.metric_name,
        metric_value=attempt.metric_value,
        higher_is_better=attempt.higher_is_better,
        failure_class=attempt.failure_class,
        root_cause=root_cause,
        candidate_strategy=strategy,
        implementation_summary=_implementation_summary(changed_files, dependency_files),
        validation_claim=candidate,
        evaluator_outcome=_evaluator_outcome(evaluator),
        dependencies=dependencies,
        dependency_files=dependency_files,
        changed_files=changed_files,
        runtime_seconds=attempt.runtime_seconds,
        branch_name=attempt.branch_name,
        commit_sha=attempt.commit_sha,
        breakthrough=breakthrough,
        breakthrough_reason=breakthrough_reason,
        lessons=lessons,
        next_recommended_actions=next_actions,
    )
    return summary


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _dependency_files(workspace: Path, task: TaskSpec) -> list[str]:
    files: list[str] = []
    requirements = Path(workspace) / task.dependency_policy.requirements_path
    if requirements.exists():
        files.append(task.dependency_policy.requirements_path)
    for rel in ("pyproject.toml", "environment.yml", "environment.yaml"):
        if (Path(workspace) / rel).exists():
            files.append(rel)
    return files


def _dependencies(workspace: Path, task: TaskSpec) -> list[str]:
    requirements = Path(workspace) / task.dependency_policy.requirements_path
    if not requirements.exists():
        return []
    deps: list[str] = []
    for line in requirements.read_text(encoding="utf-8", errors="replace").splitlines():
        cleaned = line.strip()
        if not cleaned or cleaned.startswith("#"):
            continue
        deps.append(cleaned)
    return deps


def _root_cause(
    attempt: ExperimentResult,
    evaluator: dict[str, Any],
    implementation: dict[str, Any],
) -> str | None:
    if attempt.status == AttemptStatus.SUCCESS:
        return None
    diagnostics = evaluator.get("diagnostics", {}) if isinstance(evaluator.get("diagnostics"), dict) else {}
    error = diagnostics.get("error")
    if attempt.failure_class == "dependency_install_failed":
        return "Candidate dependency installation failed before evaluator execution."
    if error == "entrypoint_timeout":
        return "Candidate entrypoint exceeded the evaluator runtime budget before producing a scored result."
    if error == "entrypoint_failed":
        run_stderr = diagnostics.get("run_stderr", "")
        if "ModuleNotFoundError" in run_stderr:
            return "Candidate imported a package that was not declared or installable in the evaluation container."
        return "Candidate entrypoint exited nonzero during evaluator execution."
    if error == "missing_submission":
        return "Candidate did not create the required submission file."
    if error == "row_order_or_content_mismatch":
        return "Candidate submission did not preserve the required row order or immutable columns."
    if error == "row_count_mismatch":
        return "Candidate submission row count did not match the evaluator contract."
    if attempt.failure_class == "codex_timeout":
        return "Codex implementation worker exceeded its wall-clock timeout before completing the work order."
    if implementation.get("status") == "failed":
        return "Codex implementation worker exited with a nonzero status."
    return attempt.failure_reason or attempt.failure_class


def _lessons(
    attempt: ExperimentResult,
    root_cause: str | None,
    dependencies: list[str],
    candidate: dict[str, Any],
) -> list[str]:
    lessons: list[str] = []
    if attempt.status == AttemptStatus.SUCCESS:
        lessons.append("This branch produced a valid authoritative evaluator score.")
    if root_cause:
        lessons.append(root_cause)
    if dependencies:
        lessons.append("Candidate declared runtime dependencies for clean-container evaluation.")
    elif attempt.status != AttemptStatus.SUCCESS:
        lessons.append("No requirements.txt was declared for this attempt.")
    if candidate.get("validation_strategy"):
        lessons.append(f"Candidate validation claim: {candidate['validation_strategy']}")
    return lessons


def _next_actions(attempt: ExperimentResult, root_cause: str | None) -> list[str]:
    if attempt.status == AttemptStatus.SUCCESS:
        return [
            "Explore one targeted improvement from this branch.",
            "Run an ablation if complexity increased significantly.",
        ]
    if root_cause and "dependency" in root_cause.lower():
        return ["Run a debug attempt that fixes dependency declaration and clean-container execution."]
    if root_cause and "runtime" in root_cause.lower():
        return ["Run a debug attempt that keeps the strategy but makes execution complete reliably."]
    if root_cause and "submission" in root_cause.lower():
        return ["Run a debug attempt focused on output contract compliance."]
    return ["Run a debug attempt from this branch if the implementation appears promising."]


def _breakthrough(
    *,
    task: TaskSpec,
    attempt: ExperimentResult,
    store: ExperimentStore,
    root_cause: str | None,
) -> tuple[bool, str | None]:
    if attempt.status != AttemptStatus.SUCCESS or attempt.metric_value is None:
        return False, None
    previous = [
        item
        for item in store.list_attempts(task.task_id)
        if item.attempt_id != attempt.attempt_id and item.created_at < attempt.created_at
    ]
    previous_successes = [
        item for item in previous if item.status == AttemptStatus.SUCCESS and item.metric_value is not None
    ]
    if not previous_successes:
        return True, "first_success"
    best_previous = (
        max(previous_successes, key=lambda item: item.metric_value)
        if task.higher_is_better
        else min(previous_successes, key=lambda item: item.metric_value)
    )
    delta = attempt.metric_value - best_previous.metric_value
    if not task.higher_is_better:
        delta = -delta
    if delta >= 0.01:
        return True, f"improved_best_by_{delta:.4f}"
    if root_cause is None and attempt.runtime_seconds and best_previous.runtime_seconds:
        if attempt.runtime_seconds < best_previous.runtime_seconds * 0.75:
            return True, "similar_score_faster_runtime"
    return False, None


def _candidate_strategy(candidate: dict[str, Any], objective: str | None) -> str | None:
    notes = candidate.get("notes")
    validation = candidate.get("validation_strategy")
    parts = [part for part in [objective, validation, notes] if isinstance(part, str) and part]
    return " | ".join(parts) if parts else None


def _implementation_summary(changed_files: list[str], dependency_files: list[str]) -> str:
    parts = []
    if changed_files:
        parts.append("Changed files: " + ", ".join(changed_files[:12]))
    if dependency_files:
        parts.append("Declared dependency files: " + ", ".join(dependency_files))
    return "; ".join(parts) if parts else "No changed files were captured."


def _evaluator_outcome(evaluator: dict[str, Any]) -> dict[str, Any]:
    if not evaluator:
        return {}
    return {
        "status": evaluator.get("status"),
        "metric_name": evaluator.get("metric_name"),
        "metric_value": evaluator.get("metric_value"),
        "valid": evaluator.get("valid"),
        "failure_class": evaluator.get("failure_class"),
        "diagnostics": evaluator.get("diagnostics", {}),
    }
