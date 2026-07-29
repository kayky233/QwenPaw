"""Delivery contracts for commit, push, and change-request publication."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .candidate_checkpoint import CandidateCheckpoint
from .change_request_delivery import ChangeRequestResult
from .contracts import ResearchArtifactContract, ResearchArtifactType
from .episode_package import EpisodePackage


@dataclass(frozen=True)
class CampaignPublication:
    """Verified Git publication created before opening a PR or MR."""

    commit_sha: str
    head_branch: str
    artifact: ResearchArtifactContract

    def validate(self, run_id: str) -> None:
        if not self.commit_sha.strip():
            raise ValueError("campaign publication requires a commit SHA")
        if not self.head_branch.strip():
            raise ValueError("campaign publication requires a head branch")
        if self.artifact.run_id != run_id:
            raise ValueError("campaign publication artifact run_id mismatch")
        if self.artifact.artifact_type != ResearchArtifactType.COMMIT:
            raise ValueError("campaign publication requires a COMMIT artifact")
        if not self.artifact.verified:
            raise ValueError("campaign publication COMMIT artifact is not verified")
        recorded = str(self.artifact.metadata.get("commit_sha", ""))
        if recorded != self.commit_sha:
            raise ValueError("campaign publication commit SHA evidence mismatch")


class CampaignPublisher(Protocol):
    async def publish(
        self,
        episode: EpisodePackage,
        checkpoint: CandidateCheckpoint,
    ) -> CampaignPublication: ...


@dataclass(frozen=True)
class CampaignDeliveryReceipt:
    """Receipt returned only after commit, push, and PR/MR creation."""

    change_request: ChangeRequestResult
    publication: CampaignPublication
    artifacts: tuple[ResearchArtifactContract, ...]

    def validate(self, run_id: str) -> None:
        self.publication.validate(run_id)
        if not self.change_request.url.strip():
            raise ValueError("delivery receipt requires a change request URL")

        verified_types = {
            artifact.artifact_type
            for artifact in self.artifacts
            if artifact.run_id == run_id and artifact.verified
        }
        missing = {
            ResearchArtifactType.COMMIT,
            ResearchArtifactType.PULL_REQUEST,
        } - verified_types
        if missing:
            values = ", ".join(sorted(item.value for item in missing))
            raise ValueError(
                "delivery receipt missing verified artifacts: " + values
            )

        commit_artifacts = tuple(
            artifact
            for artifact in self.artifacts
            if artifact.artifact_type == ResearchArtifactType.COMMIT
        )
        if self.publication.artifact not in commit_artifacts:
            raise ValueError("delivery receipt omitted the publication artifact")

        request_artifacts = tuple(
            artifact
            for artifact in self.artifacts
            if artifact.artifact_type == ResearchArtifactType.PULL_REQUEST
        )
        if not any(
            artifact.path == self.change_request.url
            for artifact in request_artifacts
        ):
            raise ValueError("change request URL evidence mismatch")
