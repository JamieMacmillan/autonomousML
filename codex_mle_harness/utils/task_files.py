"""Path helpers for files declared by a task manifest."""

from __future__ import annotations

from pathlib import Path

from codex_mle_harness.core.models import TaskSpec


RESERVED_WORKSPACE_PATHS = {
    ".goal.md",
    ".work_order.json",
    ".work_order_prompt.md",
    "task_description.md",
    "task_manifest.yaml",
}


def task_root(task: TaskSpec) -> Path | None:
    """Return the task manifest directory when the task came from a manifest."""

    return task.manifest_path.parent.resolve() if task.manifest_path is not None else None


def workspace_relative_path(value: str | Path) -> Path:
    """Validate a path that should live inside an attempt workspace."""

    path = Path(value)
    if path.is_absolute():
        raise ValueError(f"Workspace path must be relative: {value}")
    if not path.parts or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError(f"Workspace path must not contain empty or parent segments: {value}")
    return path


def support_file_destination(task: TaskSpec, source: Path) -> Path:
    """Return where a support file should be copied in an attempt workspace.

    Files below the task manifest directory keep their manifest-relative path.
    External files are copied under ``support_files/`` so they do not collide with
    task-owned files such as the description, work order, or candidate outputs.
    """

    source = Path(source).resolve()
    root = task_root(task)
    if root is not None and _is_relative_to(source, root):
        return workspace_relative_path(source.relative_to(root))
    return Path("support_files") / source.name


def support_file_destinations(task: TaskSpec) -> dict[Path, Path]:
    """Map support-file sources to their workspace-relative destinations."""

    return {Path(source).resolve(): support_file_destination(task, source) for source in task.support_files}


def is_reserved_workspace_path(path: Path) -> bool:
    """Return whether a destination would overwrite harness-owned workspace files."""

    rel = workspace_relative_path(path)
    return rel.as_posix() in RESERVED_WORKSPACE_PATHS or rel.parts[0] in {
        ".codex_mle_harness",
        ".git",
        "submission",
        "working",
    }


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True
