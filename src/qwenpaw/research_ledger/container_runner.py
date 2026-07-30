"""Containerized execution runner for isolated AutoResearch validation."""

from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from .execution_runner import (
    CommandRequest,
    CommandResult,
    ExecutionCapabilities,
)


@dataclass(frozen=True)
class ContainerPolicy:
    image: str
    engine: str = "docker"
    network_enabled: bool = False
    memory_limit: str = "4g"
    cpu_limit: str = "2"
    read_only_root: bool = True


class ContainerExecutionRunner:
    def __init__(self, policy: ContainerPolicy):
        self.policy = policy

    @property
    def capabilities(self) -> ExecutionCapabilities:
        return ExecutionCapabilities(
            platform="linux-container",
            network_access=self.policy.network_enabled,
            containerized=True,
            writable_workspace=True,
        )

    def build_command(self, request: CommandRequest) -> tuple[str, ...]:
        workspace = str(Path(request.cwd).resolve())
        command = [
            self.policy.engine,
            "run",
            "--rm",
            "--cpus",
            self.policy.cpu_limit,
            "--memory",
            self.policy.memory_limit,
            "--network",
            "bridge" if self.policy.network_enabled else "none",
            "-v",
            f"{workspace}:/workspace",
            "-w",
            "/workspace",
        ]
        if self.policy.read_only_root:
            command.append("--read-only")
            command.extend(("--tmpfs", "/tmp:rw,noexec,nosuid,size=256m"))
        for key, value in sorted(request.environment.items()):
            command.extend(("-e", f"{key}={value}"))
        command.append(self.policy.image)
        command.extend(request.argv)
        return tuple(command)

    def run(self, request: CommandRequest) -> CommandResult:
        argv = self.build_command(request)
        started = time.monotonic()
        try:
            completed = subprocess.run(
                list(argv),
                capture_output=True,
                text=True,
                check=False,
                timeout=request.timeout_seconds,
            )
            return CommandResult(
                argv=argv,
                cwd=request.cwd,
                exit_code=completed.returncode,
                stdout=completed.stdout,
                stderr=completed.stderr,
                duration_seconds=time.monotonic() - started,
                runner_name=type(self).__name__,
            )
        except subprocess.TimeoutExpired as exc:
            stdout = self._timeout_text(exc.stdout)
            stderr = self._timeout_text(exc.stderr)
            return CommandResult(
                argv=argv,
                cwd=request.cwd,
                exit_code=124,
                stdout=stdout,
                stderr=stderr,
                duration_seconds=time.monotonic() - started,
                timed_out=True,
                runner_name=type(self).__name__,
            )

    @staticmethod
    def _timeout_text(value: str | bytes | None) -> str:
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="replace")
        return value or ""
