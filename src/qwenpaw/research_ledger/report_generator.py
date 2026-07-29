"""Generate durable experiment reports from AutoResearch evidence."""

from __future__ import annotations

from .contracts import ResearchArtifactContract, ResearchArtifactType


class ExperimentReportGenerator:
    def generate(
        self,
        hypothesis: str,
        decision: str,
        artifacts: list[ResearchArtifactContract],
    ) -> ResearchArtifactContract:
        lines = [
            "# AutoResearch Experiment Report",
            "",
            "## Hypothesis",
            hypothesis,
            "",
            "## Decision",
            decision,
            "",
            "## Evidence",
        ]
        for artifact in artifacts:
            lines.append(
                f"- {artifact.artifact_type.value}: {artifact.path}"
            )
        content = "\n".join(lines)
        return ResearchArtifactContract(
            artifact_id="experiment-report",
            run_id=artifacts[0].run_id if artifacts else "",
            step_id=artifacts[0].step_id if artifacts else "",
            artifact_type=ResearchArtifactType.REPORT,
            path="research_report.md",
            content_hash=ResearchArtifactContract.hash_content(content),
            verified=True,
            metadata={"decision": decision},
        )
