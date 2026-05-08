"""Path utilities for the Codex MLE Harness."""

from pathlib import Path

HARNESS_DIR_NAME = ".codex_mle_harness"


def get_harness_dir(workspace: Path) -> Path:
    """Get the harness directory for a workspace.

    Args:
        workspace: Path to the workspace

    Returns:
        Path to the harness directory
    """
    return Path(workspace) / HARNESS_DIR_NAME


def get_work_orders_dir(workspace: Path) -> Path:
    """Get the work orders directory for a workspace.

    Args:
        workspace: Path to the workspace

    Returns:
        Path to the work orders directory
    """
    return get_harness_dir(workspace) / "work_orders"


def get_experiments_file(workspace: Path) -> Path:
    """Get the experiments file path for a workspace.

    Args:
        workspace: Path to the workspace

    Returns:
        Path to the experiments.jsonl file
    """
    return get_harness_dir(workspace) / "experiments.jsonl"
