"""Implementation worker abstraction."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from .models import ImplementationResult, TaskSpec, WorkOrder


@runtime_checkable
class ImplementationWorker(Protocol):
    """Backend that writes implementation code for a WorkOrder."""

    @property
    def name(self) -> str:
        ...

    def run(self, work_order: WorkOrder, task: TaskSpec, workspace: Path) -> ImplementationResult:
        ...


class BaseImplementationWorker:
    """Shared validation for concrete workers."""

    def __init__(self, name: str):
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    def validate(self, work_order: WorkOrder, task: TaskSpec, workspace: Path) -> None:
        if work_order.task_id != task.task_id:
            raise ValueError(
                f"WorkOrder task_id {work_order.task_id!r} does not match TaskSpec {task.task_id!r}"
            )
        if not work_order.objective.strip():
            raise ValueError("WorkOrder objective is required")
        if not Path(workspace).exists():
            raise ValueError(f"Workspace does not exist: {workspace}")

