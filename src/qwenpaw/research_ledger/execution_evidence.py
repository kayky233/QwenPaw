"""Convert command execution results into verifiable research artifacts."""

from __future__ import annotations

import json

from .contracts import ResearchArtifactContract, ResearchArtifactType
from .execution_runner import CommandResult


class ExecutionEvidenceCollector:
    def __init__(self, run_id: str):
        self.run_id = run_id

    def collect(
        self,
        step_id: str,
        artifact_id: str,
        artifact_type: ResearchArtifactType,
        result: CommandResult,
    ) -> ResearchArtifactContract:
        payload = {
            "argv": result.argv,
            "cwd": result.cwd,
            "exit_code": result.exit_code,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "duration_seconds": result.duration_seconds,
            "timed_out": result.timed_out,
            "runner_name": result.runner_name,
        }
        content = json.dumps(payload, sort_keys=True, ensure_ascii=False)
        return ResearchArtifactContract(
            artifact_id=artifact_id,
            run_id=self.run_id,
            step_id=step_id,
            artifact_type=artifact_type,
            path=f"{artifact_id}.json",
            content_hash=ResearchArtifactContract.hash_content(content),
            verified=result.succeeded,
            metadata=payload,
        )
