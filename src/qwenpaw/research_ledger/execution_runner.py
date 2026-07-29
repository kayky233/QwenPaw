"""Execution environment contracts for AutoResearch commands."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import subprocess
import time
from typing import Mapping, Protocol


@dataclass(frozen=True)
class ExecutionCapabilities:
    platform: str
    network_access: bool
    containerized: bool
    writable_workspace: bool
    supports_timeouts: bool = True


@dataclass(frozen=True)
class CommandRequest:
    argv: tuple[str, ...]
    cwd: str
    timeout_seconds: float = 300.0
    environment: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class CommandResult:
    argv: tuple[str, ...]
    cwd: str
    exit_code: int
    stdout: str
    stderr: str
    duration_seconds: float
    timed_out: bool = False
    runner_name: str = "unknown"

    @property
    def succeeded(self) -> bool:
        return self.exit_code == 0 and not self.timed_out


class ExecutionRunner(Protocol):
    @property
    def capabilities(self) -> ExecutionCapabilities: ...

    def run(self, request: CommandRequest) -> CommandResult: ...


class LocalSubprocessRunner:
    """Local runner intended for tests and explicitly trusted workspaces."""

    def __init__(self, capabilities: ExecutionCapabilities | None = None):
        self._capabilities = capabilities or ExecutionCapabilities(
            platform="local",
            network_access=True,
            containerized=False,
            writable_workspace=True,
        )

    @property
    def capabilities(self) -> ExecutionCapabilities:
        return self._capabilities

    def run(self, request: CommandRequest) -> CommandResult:
        cwd = str(Path(request.cwd).resolve())
        started = time.monotonic()
        try:
            completed = subprocess.run(
                list(request.argv),
                cwd=cwd,
                env=dict(request.environment) or None,
                capture_output=True,
                text=True,
                check=False,
                timeout=request.timeout_seconds,
            )
            return CommandResult(
                argv=request.argv,
                cwd=cwd,
                exit_code=completed.returncode,
                stdout=completed.stdout,
                stderr=completed.stderr,
                duration_seconds=time.monotonic() - started,
                runner_name=type(self).__name__,
            )
        except subprocess.TimeoutExpired as exc:
            stdout = exc.stdout.decode() if isinstance(exc.stdout, bytes) else (exc.stdout or "")
            stderr = exc.stderr.decode() if isinstance(exc.stderr, bytes) else (exc.stderr or "")
            return CommandResult(
                argv=request.argv,
                cwd=cwd,
                exit_code=124,
                stdout=stdout,
                stderr=stderr,
                duration_seconds=time.monotonic() - started,
                timed_out=True,
                runner_name=type(self).__name__,
            )
