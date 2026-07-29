"""Collect test execution results as Research artifacts."""

from __future__ import annotations

import hashlib
import subprocess

from .contracts import ResearchArtifactContract, ResearchArtifactType


class TestArtifactCollector:
    def __init__(self, run_id: str):
        self.run_id = run_id

    def from_command(
        self,
        step_id: str,
        command: list[str],
    ) -> ResearchArtifactContract:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
        )
        content = result.stdout + "\n" + result.stderr
        return ResearchArtifactContract(
            artifact_id=f"{step_id}-test",
            run_id=self.run_id,
            step_id=step_id,
            artifact_type=ResearchArtifactType.TEST_RESULT,
            path="test-result.log",
            content_hash=hashlib.sha256(content.encode()).hexdigest(),
            verified=result.returncode == 0,
            metadata={
                "command": command,
                "returncode": result.returncode,
            },
        )
