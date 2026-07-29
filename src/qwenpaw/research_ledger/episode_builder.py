"""Build reproducible Episode Packages from prepared issue evidence."""

from __future__ import annotations

from .contracts import ResearchArtifactType
from .episode_package import (
    EpisodeCommand,
    EpisodeExpectedArtifact,
    EpisodePackage,
)
from .issue_solver_service import PreparedIssueSolve


class IssueEpisodeBuilder:
    """Convert issue intake and graph localization into an executable package."""

    def build(
        self,
        prepared: PreparedIssueSolve,
        *,
        run_id: str,
        base_revision: str,
        task_type: str,
        acceptance_criteria: tuple[str, ...],
        commands: tuple[EpisodeCommand, ...],
        frozen_files: tuple[str, ...] = (),
        environment: dict[str, str] | None = None,
        modifiable_files: tuple[str, ...] = (),
    ) -> EpisodePackage:
        evidence = prepared.evidence
        context = prepared.contextual_plan.context_pack
        localized_paths = tuple(
            dict.fromkeys((*context.affected_paths, *context.test_paths))
        )
        approved_paths = tuple(dict.fromkeys(modifiable_files))
        selected_paths = approved_paths or localized_paths
        if not selected_paths:
            raise ValueError(
                "issue localization produced no modifiable paths; "
                "explicit scope approval is required"
            )

        plan = prepared.contextual_plan.plan
        package = EpisodePackage(
            episode_id=f"{run_id}-issue-{evidence.number}",
            run_id=run_id,
            task_type=task_type,
            repository=evidence.repository,
            issue_number=evidence.number,
            goal=evidence.title,
            base_revision=base_revision,
            acceptance_criteria=acceptance_criteria,
            modifiable_files=selected_paths,
            frozen_files=frozen_files,
            commands=commands,
            expected_artifacts=(
                EpisodeExpectedArtifact(
                    ResearchArtifactType.PLAN,
                    "plan",
                ),
                EpisodeExpectedArtifact(
                    ResearchArtifactType.CODE_DIFF,
                    "implement",
                ),
                EpisodeExpectedArtifact(
                    ResearchArtifactType.TEST_RESULT,
                    "test",
                ),
                EpisodeExpectedArtifact(
                    ResearchArtifactType.REPORT,
                    "review",
                ),
                EpisodeExpectedArtifact(
                    ResearchArtifactType.COMMIT,
                    "delivery",
                ),
            ),
            environment=environment or {},
            metadata={
                "issue_state": evidence.state,
                "issue_labels": list(evidence.labels),
                "issue_comment_count": len(evidence.comments),
                "linked_pull_requests": list(evidence.linked_pull_requests),
                "graph_available": context.graph_available,
                "context_truncated": context.truncated,
                "context_estimated_tokens": context.estimated_tokens,
                "context_downgrade_reason": context.downgrade_reason,
                "scope_source": (
                    "explicit_approval" if approved_paths else "repository_graph"
                ),
                "implementation_steps": list(plan.implementation_steps),
                "validation_steps": list(plan.validation_steps),
            },
        )
        package.validate()
        return package
