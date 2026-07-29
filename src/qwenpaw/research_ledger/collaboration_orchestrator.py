"""Coordinate Task Envelopes, file leases, and artifact handoffs."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Awaitable, Callable

from .collaboration_contracts import ArtifactHandoff, TaskEnvelope
from .contracts import ResearchArtifactContract
from .file_lease import FileLeaseRegistry
from .step_repository import ResearchArtifactRepository


class CollaborationContractError(RuntimeError):
    pass


class CollaborationOrchestrator:
    def __init__(
        self,
        artifacts: ResearchArtifactRepository,
        leases: FileLeaseRegistry | None = None,
    ) -> None:
        self.artifacts = artifacts
        self.leases = leases or FileLeaseRegistry()

    async def delegate(
        self,
        envelope: TaskEnvelope,
        *,
        agent_id: str,
        transport: Callable[
            [str, TaskEnvelope],
            Awaitable[list[ResearchArtifactContract]],
        ],
    ) -> ArtifactHandoff:
        if envelope.allowed_paths and not envelope.read_only:
            self.leases.acquire(
                envelope.allowed_paths,
                owner=agent_id,
                run_id=envelope.run_id,
                step_id=envelope.step_id,
            )

        try:
            produced = await transport(agent_id, envelope)
            for artifact in produced:
                if artifact.run_id != envelope.run_id:
                    raise CollaborationContractError(
                        "agent returned artifact for a different run"
                    )
                if artifact.step_id != envelope.step_id:
                    raise CollaborationContractError(
                        "agent returned artifact for a different step"
                    )
                self.artifacts.add(artifact)

            produced_types = {artifact.artifact_type for artifact in produced}
            missing = tuple(
                item
                for item in envelope.expected_artifact_types
                if item not in produced_types
            )
            if missing:
                raise CollaborationContractError(
                    "agent handoff missing expected artifacts: "
                    + ", ".join(item.value for item in missing)
                )

            return ArtifactHandoff(
                task_id=envelope.task_id,
                run_id=envelope.run_id,
                step_id=envelope.step_id,
                producer_role=envelope.role,
                artifact_ids=tuple(item.artifact_id for item in produced),
                summary=(
                    f"{envelope.role.value} produced {len(produced)} artifact(s)"
                ),
                completed_at=datetime.now(timezone.utc),
            )
        finally:
            if envelope.allowed_paths and not envelope.read_only:
                self.leases.release_owner(agent_id, run_id=envelope.run_id)
