"""Robust git helpers for branch-per-attempt workspaces."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from codex_mle_harness.core.models import slugify


def _run_git(path: Path, args: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=path,
        text=True,
        capture_output=True,
        check=check,
    )


def ensure_git_repo(path: Path) -> None:
    """Ensure ``path`` is a git repo with at least one baseline commit."""

    path = Path(path)
    path.mkdir(parents=True, exist_ok=True)
    if not (path / ".git").exists():
        _run_git(path, ["init"])
    _ensure_git_identity(path)
    if get_head_commit(path) is None:
        _run_git(path, ["commit", "--allow-empty", "-m", "harness baseline"])


def _ensure_git_identity(path: Path) -> None:
    email = _run_git(path, ["config", "user.email"], check=False)
    if email.returncode != 0 or not email.stdout.strip():
        _run_git(path, ["config", "user.email", "harness@codex-mle.local"])
    name = _run_git(path, ["config", "user.name"], check=False)
    if name.returncode != 0 or not name.stdout.strip():
        _run_git(path, ["config", "user.name", "Codex MLE Harness"])


def get_head_commit(path: Path) -> str | None:
    result = _run_git(Path(path), ["rev-parse", "HEAD"], check=False)
    return result.stdout.strip() if result.returncode == 0 else None


def get_current_branch(path: Path) -> str | None:
    result = _run_git(Path(path), ["branch", "--show-current"], check=False)
    return result.stdout.strip() if result.returncode == 0 else None


def create_branch(path: Path, branch_name: str, start_point: str | None = None) -> str:
    """Create or reset a branch and check it out."""

    branch = slugify(branch_name, fallback="attempt")
    args = ["checkout", "-B", branch]
    if start_point:
        args.append(start_point)
    _run_git(Path(path), args)
    return branch


def prepare_worktree(repo_path: Path, workspace_path: Path, branch_name: str, parent_ref: str = "HEAD") -> str:
    """Create a fresh git worktree for an attempt branch."""

    repo_path = Path(repo_path)
    workspace_path = Path(workspace_path)
    ensure_git_repo(repo_path)
    if workspace_path.exists():
        shutil.rmtree(workspace_path)
    workspace_path.parent.mkdir(parents=True, exist_ok=True)
    branch = slugify(branch_name, fallback="attempt")
    _run_git(repo_path, ["worktree", "prune"], check=False)
    _run_git(repo_path, ["worktree", "add", "-B", branch, str(workspace_path), parent_ref])
    _ensure_git_identity(workspace_path)
    return branch


def force_branch(path: Path, branch_name: str, target_ref: str) -> str:
    """Create or move ``branch_name`` to ``target_ref`` without checking it out."""

    path = Path(path)
    ensure_git_repo(path)
    branch = slugify(branch_name, fallback="adopted")
    _run_git(path, ["branch", "-f", branch, target_ref])
    return branch


def branch_commit(path: Path, branch_name: str) -> str | None:
    """Return the commit sha for a branch or ref."""

    result = _run_git(Path(path), ["rev-parse", branch_name], check=False)
    return result.stdout.strip() if result.returncode == 0 else None


def commit_all(path: Path, message: str) -> str | None:
    """Commit all current changes and return the resulting commit sha."""

    path = Path(path)
    _run_git(path, ["add", "-A"])
    if not get_changed_files(path):
        return get_head_commit(path)
    result = _run_git(path, ["commit", "-m", message], check=False)
    if result.returncode != 0:
        status = _run_git(path, ["status", "--porcelain"], check=False)
        if not status.stdout.strip():
            return get_head_commit(path)
        raise RuntimeError(f"git commit failed: {result.stderr.strip()}")
    return get_head_commit(path)


def get_changed_files(path: Path) -> list[str]:
    """Return modified, deleted, staged, and untracked files."""

    result = _run_git(Path(path), ["status", "--porcelain=v1", "-z"], check=True)
    entries = [entry for entry in result.stdout.split("\0") if entry]
    files: list[str] = []
    idx = 0
    while idx < len(entries):
        entry = entries[idx]
        status = entry[:2]
        file_path = entry[3:] if len(entry) > 3 else ""
        if status.startswith("R") or status.startswith("C"):
            files.append(file_path)
            idx += 2
        else:
            files.append(file_path)
            idx += 1
    return sorted(set(files))


def _untracked_files(path: Path) -> list[str]:
    result = _run_git(Path(path), ["ls-files", "--others", "--exclude-standard", "-z"])
    return sorted(entry for entry in result.stdout.split("\0") if entry)


def get_git_diff(path: Path) -> str:
    """Return a patch including tracked, staged, and untracked files."""

    path = Path(path)
    parts: list[str] = []
    for args in (["diff", "--binary"], ["diff", "--cached", "--binary"]):
        result = _run_git(path, args, check=True)
        if result.stdout:
            parts.append(result.stdout)
    for rel in _untracked_files(path):
        file_path = path / rel
        if file_path.is_dir():
            continue
        result = subprocess.run(
            ["git", "diff", "--no-index", "--binary", "--", os.devnull, str(file_path)],
            cwd=path,
            text=True,
            capture_output=True,
            check=False,
        )
        if result.stdout:
            parts.append(result.stdout.replace(str(file_path), f"b/{rel}"))
    return "\n".join(part.rstrip("\n") for part in parts if part).strip() + ("\n" if parts else "")


def save_patch(path: Path, output_path: Path) -> None:
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(get_git_diff(Path(path)), encoding="utf-8")
