"""Automatic collection helpers for AutoResearch evidence artifacts."""

from __future__ import annotations

import hashlib
from pathlib import Path

from .contracts import ResearchArtifactContract, ResearchArtifactType


class ArtifactCollector:
    def __init__(self, run_id: str):
        self.run_id = run_id

    def from_text(
        self,
        step_id: str,
        artifact_id: str,
        artifact_type: ResearchArtifactType,
        path: str,
        content: str,
        verified: bool = False,
    ) -> ResearchArtifactContract:
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        return ResearchArtifactContract(
            artifact_id=artifact_id,
            run_id=self.run_id,
            step_id=step_id,
            artifact_type=artifact_type,
            path=path,
            content_hash=digest,
            verified=verified,
        )

    def from_file(
        self,
        step_id: str,
        artifact_id: str,
        artifact_type: ResearchArtifactType,
        path: str,
    ) -> ResearchArtifactContract:
        content = Path(path).read_text(encoding="utf-8")
        return self.from_text(
            step_id,
            artifact_id,
            artifact_type,
            path,
            content,
            True,
        )
