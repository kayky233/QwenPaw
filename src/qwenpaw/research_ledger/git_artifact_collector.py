"""Collect source changes as verifiable Research artifacts."""

from __future__ import annotations

import hashlib
import subprocess

from .contracts import ResearchArtifactContract, ResearchArtifactType


class GitArtifactCollector:
    def __init__(self, run_id: str):
        self.run_id = run_id

    def collect_diff(self, step_id: str) -> ResearchArtifactContract:
        result = subprocess.run(
            ["git", "diff"],
            capture_output=True,
            text=True,
            check=False,
        )
        content = result.stdout
        return ResearchArtifactContract(
            artifact_id=f"{step_id}-diff",
            run_id=self.run_id,
            step_id=step_id,
            artifact_type=ResearchArtifactType.CODE_DIFF,
            path="git.diff",
            content_hash=hashlib.sha256(content.encode()).hexdigest(),
            verified=result.returncode == 0,
            metadata={"returncode": result.returncode},
        )
