"""Patch lifecycle management for AutoResearch experiments."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PatchCheckpoint:
    experiment_id: str
    base_revision: str


class PatchManager:
    """Abstract patch manager.

    Git operations are intentionally injected later so runtime tests do not
    depend on a real repository checkout.
    """

    def __init__(self, git_backend=None):
        self.git_backend = git_backend

    def create_checkpoint(self, experiment_id: str, revision: str) -> PatchCheckpoint:
        return PatchCheckpoint(experiment_id, revision)

    def keep(self, checkpoint: PatchCheckpoint) -> str:
        if self.git_backend:
            return self.git_backend.commit_experiment(checkpoint.experiment_id)
        return checkpoint.base_revision

    def revert(self, checkpoint: PatchCheckpoint) -> str:
        if self.git_backend:
            return self.git_backend.checkout(checkpoint.base_revision)
        return checkpoint.base_revision
