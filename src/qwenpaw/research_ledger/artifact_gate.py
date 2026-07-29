"""Gate delivery on verified research evidence."""

from __future__ import annotations

from dataclasses import dataclass

from .contracts import ResearchArtifactType
from .step_repository import ResearchArtifactRepository


@dataclass(frozen=True)
class ArtifactGateResult:
    allowed: bool
    missing: tuple[ResearchArtifactType, ...] = ()


def check_required_artifacts(
    repository: ResearchArtifactRepository,
    step_id: str,
    required: tuple[ResearchArtifactType, ...],
) -> ArtifactGateResult:
    missing = tuple(
        item
        for item in required
        if not repository.has_verified_type(step_id, item)
    )
    return ArtifactGateResult(
        allowed=not missing,
        missing=missing,
    )
