"""Docker execution for deterministic evaluator runs."""

from __future__ import annotations

import os
import re
import shlex
import subprocess
import time
from pathlib import Path

from codex_mle_harness.core.models import ContainerMetadata, ResourceLimits, TaskSpec

_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class DockerRunResult:
    """Small value object returned by DockerRunner."""

    def __init__(
        self,
        *,
        exit_code: int,
        stdout: str,
        stderr: str,
        runtime_seconds: float,
        metadata: ContainerMetadata,
        timed_out: bool = False,
    ):
        self.exit_code = exit_code
        self.stdout = stdout
        self.stderr = stderr
        self.runtime_seconds = runtime_seconds
        self.metadata = metadata
        self.timed_out = timed_out


class DockerRunner:
    """Run evaluator commands in fresh Docker containers."""

    def __init__(self, *, pass_env: bool | None = None):
        self.pass_env = pass_env

    def run(
        self,
        *,
        task: TaskSpec,
        workspace: Path,
        command: str,
        timeout_seconds: int,
        container_name: str | None = None,
    ) -> DockerRunResult:
        workspace = Path(workspace).resolve()
        volumes = {str(workspace): "/workspace"}
        args = [
            "docker",
            "run",
            "--rm",
            "--network",
            "bridge",
            "-w",
            "/workspace",
            "-v",
            f"{workspace}:/workspace:rw",
        ]
        if task.manifest_path is not None:
            task_root = task.manifest_path.parent.resolve()
            args.extend(["-v", f"{task_root}:/task:ro"])
            volumes[str(task_root)] = "/task:ro"
        for mount in task.data_mounts:
            source = mount.source.resolve()
            target = mount.target
            container_target = target if target.startswith("/") else f"/workspace/{target}"
            mode = "ro" if mount.read_only else "rw"
            volumes[str(source)] = f"{container_target}:{mode}"
            args.extend(["-v", f"{source}:{container_target}:{mode}"])
        resources: ResourceLimits = task.resources
        if resources.memory_limit:
            args.extend(["--memory", resources.memory_limit])
        if resources.cpu_limit and resources.cpu_limit > 0:
            args.extend(["--cpus", str(resources.cpu_limit)])
        if resources.gpu_devices:
            gpu_value = (
                ",".join(resources.gpu_devices)
                if isinstance(resources.gpu_devices, list)
                else str(resources.gpu_devices)
            )
            args.extend(["--gpus", f"device={gpu_value}" if gpu_value != "all" else "all"])
        if container_name:
            args.extend(["--name", container_name])
        env_count = 0
        pass_all = task.environment.pass_all if self.pass_env is None else self.pass_env
        env_names = sorted(os.environ) if pass_all else sorted(set(task.environment.allowlist))
        for key in env_names:
            if key in os.environ and _ENV_NAME.match(key):
                args.extend(["-e", key])
                env_count += 1
        full_command = command
        dependency_command = self._dependency_install_command(task)
        if dependency_command:
            full_command = f"{dependency_command} && {full_command}"
        if task.setup_command:
            full_command = f"{task.setup_command} && {full_command}"
        args.extend([task.docker_image, "bash", "-lc", full_command])
        metadata = ContainerMetadata(
            image=task.docker_image,
            container_name=container_name,
            command=full_command,
            volumes=volumes,
            environment_count=env_count,
            memory_limit=resources.memory_limit,
            cpu_limit=resources.cpu_limit,
            gpu_devices=resources.gpu_devices,
        )
        start = time.monotonic()
        try:
            result = subprocess.run(
                args,
                cwd=workspace,
                text=True,
                capture_output=True,
                timeout=timeout_seconds,
                check=False,
            )
            return DockerRunResult(
                exit_code=result.returncode,
                stdout=result.stdout,
                stderr=result.stderr,
                runtime_seconds=time.monotonic() - start,
                metadata=metadata,
            )
        except subprocess.TimeoutExpired as exc:
            return DockerRunResult(
                exit_code=124,
                stdout=exc.stdout or "",
                stderr=exc.stderr or "Docker command timed out",
                runtime_seconds=time.monotonic() - start,
                metadata=metadata,
                timed_out=True,
            )

    def _dependency_install_command(self, task: TaskSpec) -> str | None:
        policy = task.dependency_policy
        if not policy.allow_requirements_txt:
            return None
        requirements_path = policy.requirements_path
        install_command = policy.install_command.format(
            requirements_path=shlex.quote(requirements_path)
        )
        dep_dir = ".codex_mle_harness"
        stdout_path = f"{dep_dir}/dependency_install_stdout.txt"
        stderr_path = f"{dep_dir}/dependency_install_stderr.txt"
        exit_path = f"{dep_dir}/dependency_install_exit_code.txt"
        return (
            f"mkdir -p {shlex.quote(dep_dir)} && "
            f"if [ -f {shlex.quote(requirements_path)} ]; then "
            f"timeout {int(policy.install_timeout_seconds)} bash -lc {shlex.quote(install_command)} "
            f"> {shlex.quote(stdout_path)} 2> {shlex.quote(stderr_path)}; "
            f"status=$?; echo $status > {shlex.quote(exit_path)}; "
            f"if [ $status -ne 0 ]; then exit {int(policy.failure_exit_code)}; fi; "
            f"else echo 'requirements file not present' > {shlex.quote(stdout_path)}; "
            f": > {shlex.quote(stderr_path)}; echo 0 > {shlex.quote(exit_path)}; fi"
        )
