"""In-memory repositories for Step/Artifact contracts.

The first stage intentionally keeps storage independent from ORM migration.
The same interface can later be backed by Research Ledger tables.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Iterable

from .contracts import (
    ResearchArtifactContract,
    ResearchArtifactType,
    ResearchStepContract,
    ResearchStepStatus,
)


class ResearchStepRepository:
    def __init__(self) -> None:
        self._steps: dict[str, ResearchStepContract] = {}

    def create(self, step: ResearchStepContract) -> ResearchStepContract:
        self._steps[step.step_id] = step
        return step

    def get(self, step_id: str) -> ResearchStepContract | None:
        return self._steps.get(step_id)

    def update_status(
        self,
        step_id: str,
        status: ResearchStepStatus,
    ) -> ResearchStepContract:
        current = self._steps[step_id]
        updated = replace(current, status=status)
        self._steps[step_id] = updated
        return updated

    def list_for_run(self, run_id: str) -> list[ResearchStepContract]:
        return sorted(
            (item for item in self._steps.values() if item.run_id == run_id),
            key=lambda item: item.index,
        )


class ResearchArtifactRepository:
    def __init__(self) -> None:
        self._artifacts: dict[str, ResearchArtifactContract] = {}

    def add(self, artifact: ResearchArtifactContract) -> ResearchArtifactContract:
        self._artifacts[artifact.artifact_id] = artifact
        return artifact

    def get(self, artifact_id: str) -> ResearchArtifactContract | None:
        return self._artifacts.get(artifact_id)

    def list_for_step(self, step_id: str) -> list[ResearchArtifactContract]:
        return [
            item for item in self._artifacts.values()
            if item.step_id == step_id
        ]

    def has_verified_type(
        self,
        step_id: str,
        artifact_type: ResearchArtifactType,
    ) -> bool:
        return any(
            item.step_id == step_id
            and item.artifact_type == artifact_type
            and item.verified
            for item in self._artifacts.values()
        )

    def all_verified(
        self,
        step_id: str,
        required: Iterable[ResearchArtifactType],
    ) -> bool:
        return all(
            self.has_verified_type(step_id, artifact_type)
            for artifact_type in required
        )
