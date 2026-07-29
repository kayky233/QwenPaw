"""Delivery receipt returned after commit, push, and change-request creation."""

from __future__ import annotations

from dataclasses import dataclass

from .change_request_delivery import ChangeRequestResult
from .contracts import ResearchArtifactContract, ResearchArtifactType


@dataclass(frozen=True)
class CampaignDeliveryReceipt:
    change_request: ChangeRequestResult
    artifacts: tuple[ResearchArtifactContract, ...]

    def validate(self, run_id: str) -> None:
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
