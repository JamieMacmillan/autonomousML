"""Production Codex CLI implementation worker."""

from __future__ import annotations

import shutil
import subprocess
import time
from pathlib import Path

from codex_mle_harness.core.implementation_worker import BaseImplementationWorker
from codex_mle_harness.core.models import (
    FailureClass,
    ImplementationResult,
    ImplementationStatus,
    TaskSpec,
    WorkOrder,
)
from codex_mle_harness.core.work_order import write_work_order_files
from codex_mle_harness.utils.git_utils import get_changed_files, get_current_branch, get_git_diff


class CodexWorker(BaseImplementationWorker):
    """Invoke Codex CLI against a prepared attempt workspace."""

    def __init__(
        self,
        *,
        command_prefix: list[str] | None = None,
        capture_json: bool = True,
        mode: str | None = None,
    ):
        super().__init__("codex")
        self.command_prefix = command_prefix
        self.capture_json = capture_json
        self.mode = mode

    def _command(
        self,
        workspace: Path,
        prompt_path: Path,
        output_path: Path | None,
        *,
        goal_file: Path | None = None,
        use_goal: bool = False,
    ) -> list[str]:
        if self.command_prefix is not None:
            return [*self.command_prefix, str(prompt_path)]
        codex = shutil.which("codex")
        if not codex:
            raise FileNotFoundError("Codex CLI is not installed or not on PATH")
        args = [
            codex,
            "exec",
            "-C",
            str(workspace),
            "--full-auto",
        ]
        if use_goal and goal_file is not None:
            goal_flag = self._goal_flag()
            if goal_flag:
                args.extend([goal_flag, str(goal_file)])
        if self.capture_json:
            args.append("--json")
        if output_path is not None:
            args.extend(["-o", str(output_path)])
        args.append("-")
        return args

    def run(self, work_order: WorkOrder, task: TaskSpec, workspace: Path) -> ImplementationResult:
        workspace = Path(workspace)
        self.validate(work_order, task, workspace)
        _, prompt_path = write_work_order_files(work_order, task, workspace)
        goal_path = workspace / ".goal.md"
        final_message_path = workspace / ".codex_final_message.txt"
        requested_mode = self.mode or task.implementation_worker.mode
        goal_supported = self._goal_flag() is not None
        if (
            requested_mode == "goal"
            and not goal_supported
            and self.command_prefix is None
            and not task.implementation_worker.fallback_to_exec
        ):
            return ImplementationResult(
                work_order_id=work_order.work_order_id,
                status=ImplementationStatus.FAILED,
                exit_code=-1,
                stderr="Codex goal mode requested, but this Codex CLI does not expose a goal flag.",
                changed_files=get_changed_files(workspace),
                patch=get_git_diff(workspace) if get_changed_files(workspace) else "",
                branch_name=get_current_branch(workspace),
                failure_class=FailureClass.CODEX_CLI_ERROR.value,
                notes=f"requested_mode={requested_mode}; goal_supported={goal_supported}",
            )
        use_goal = requested_mode == "goal" and goal_supported
        if requested_mode == "auto" and goal_supported:
            use_goal = True
        active_prompt_path = goal_path if use_goal or (self.command_prefix is not None and requested_mode == "goal") else prompt_path
        command = self._command(
            workspace,
            active_prompt_path,
            final_message_path,
            goal_file=goal_path,
            use_goal=use_goal,
        )
        start = time.monotonic()
        try:
            prompt = prompt_path.read_text(encoding="utf-8")
            result = subprocess.run(
                command,
                cwd=workspace,
                input=prompt,
                text=True,
                capture_output=True,
                timeout=work_order.timeout_seconds,
                check=False,
            )
            runtime = time.monotonic() - start
            changed_files = get_changed_files(workspace)
            patch = get_git_diff(workspace) if changed_files else ""
            status = ImplementationStatus.SUCCESS if result.returncode == 0 else ImplementationStatus.FAILED
            return ImplementationResult(
                work_order_id=work_order.work_order_id,
                status=status,
                exit_code=result.returncode,
                stdout=result.stdout,
                stderr=result.stderr,
                changed_files=changed_files,
                patch=patch,
                branch_name=get_current_branch(workspace),
                runtime_seconds=runtime,
                failure_class=None if status == ImplementationStatus.SUCCESS else FailureClass.CODEX_CLI_ERROR.value,
                notes=self._notes(command, requested_mode=requested_mode, goal_supported=goal_supported),
            )
        except subprocess.TimeoutExpired as exc:
            runtime = time.monotonic() - start
            changed_files = get_changed_files(workspace)
            patch = get_git_diff(workspace) if changed_files else ""
            return ImplementationResult(
                work_order_id=work_order.work_order_id,
                status=ImplementationStatus.TIMEOUT,
                exit_code=124,
                stdout=exc.stdout or "",
                stderr=exc.stderr or "Codex command timed out",
                changed_files=changed_files,
                patch=patch,
                branch_name=get_current_branch(workspace),
                runtime_seconds=runtime,
                failure_class=FailureClass.CODEX_TIMEOUT.value,
                notes=self._notes(command, requested_mode=requested_mode, goal_supported=goal_supported),
            )
        except Exception as exc:
            runtime = time.monotonic() - start
            failure_class = (
                FailureClass.CODEX_CLI_MISSING.value
                if isinstance(exc, FileNotFoundError)
                else FailureClass.CODEX_CLI_ERROR.value
            )
            return ImplementationResult(
                work_order_id=work_order.work_order_id,
                status=ImplementationStatus.FAILED,
                exit_code=-1,
                stdout="",
                stderr=str(exc),
                changed_files=[],
                patch=None,
                branch_name=get_current_branch(workspace),
                runtime_seconds=runtime,
                failure_class=failure_class,
                notes=f"requested_mode={requested_mode}; goal_supported={goal_supported}; error={type(exc).__name__}",
            )

    def _goal_flag(self) -> str | None:
        help_text = _codex_exec_help()
        if "--goal-file" in help_text:
            return "--goal-file"
        if "--goal" in help_text:
            return "--goal"
        return None

    def _notes(self, command: list[str], *, requested_mode: str, goal_supported: bool) -> str:
        active_mode = "goal" if "--goal" in command or "--goal-file" in command else "exec"
        return (
            f"requested_mode={requested_mode}; active_mode={active_mode}; "
            f"goal_supported={goal_supported}; command={' '.join(command)}"
        )


_CODEX_EXEC_HELP: str | None = None


def _codex_exec_help() -> str:
    global _CODEX_EXEC_HELP
    if _CODEX_EXEC_HELP is not None:
        return _CODEX_EXEC_HELP
    codex = shutil.which("codex")
    if not codex:
        _CODEX_EXEC_HELP = ""
        return _CODEX_EXEC_HELP
    result = subprocess.run(
        [codex, "exec", "--help"],
        text=True,
        capture_output=True,
        check=False,
    )
    _CODEX_EXEC_HELP = (result.stdout or "") + (result.stderr or "")
    return _CODEX_EXEC_HELP
