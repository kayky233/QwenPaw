"""Git backend abstraction for experiment keep and revert actions."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class GitOperationResult:
    operation: str
    revision: str
    success: bool


class GitBackend:
    def __init__(self, command_runner=None):
        self.command_runner = command_runner

    def commit_experiment(self, experiment_id: str) -> str:
        if self.command_runner:
            return self.command_runner(["git", "commit", "-am", f"research: {experiment_id}"])
        return f"commit-{experiment_id}"

    def checkout(self, revision: str) -> str:
        if self.command_runner:
            return self.command_runner(["git", "checkout", revision])
        return revision
