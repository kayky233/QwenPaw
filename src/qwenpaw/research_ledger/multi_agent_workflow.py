"""First durable Planner -> Implementer -> Reviewer collaboration workflow."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable

from .collaboration_contracts import (
    ArtifactHandoff,
    ResearchAgentRole,
    ReviewDecision,
    ReviewVerdict,
    TaskEnvelope,
)
from .collaboration_orchestrator import CollaborationOrchestrator
from .contracts import ResearchArtifactContract, ResearchArtifactType


@dataclass(frozen=True)
class MultiAgentAssignments:
    planner: str
    implementer: str
    reviewer: str


@dataclass(frozen=True)
class MultiAgentWorkflowResult:
    planner_handoff: ArtifactHandoff
    implementer_handoff: ArtifactHandoff
    reviewer_handoff: ArtifactHandoff
    review: ReviewDecision


AgentTransport = Callable[
    [str, TaskEnvelope],
    Awaitable[list[ResearchArtifactContract]],
]
ReviewParser = Callable[[tuple[str, ...]], ReviewDecision]


class MultiAgentResearchWorkflow:
    def __init__(
        self,
        orchestrator: CollaborationOrchestrator,
        *,
        max_review_repairs: int = 2,
    ) -> None:
        self.orchestrator = orchestrator
        self.max_review_repairs = max_review_repairs

    async def run(
        self,
        *,
        task_id: str,
        run_id: str,
        objective: str,
        allowed_paths: tuple[str, ...],
        assignments: MultiAgentAssignments,
        transport: AgentTransport,
        review_parser: ReviewParser,
    ) -> MultiAgentWorkflowResult:
        planner_envelope = TaskEnvelope(
            task_id=task_id,
            run_id=run_id,
            step_id=f"{run_id}-plan",
            role=ResearchAgentRole.PLANNER,
            objective=objective,
            expected_artifact_types=(ResearchArtifactType.PLAN,),
            read_only=True,
        )
        planner_handoff = await self.orchestrator.delegate(
            planner_envelope,
            agent_id=assignments.planner,
            transport=transport,
        )

        implementer_envelope = TaskEnvelope(
            task_id=task_id,
            run_id=run_id,
            step_id=f"{run_id}-implement",
            role=ResearchAgentRole.IMPLEMENTER,
            objective=objective,
            input_artifact_ids=planner_handoff.artifact_ids,
            expected_artifact_types=(ResearchArtifactType.CODE_DIFF,),
            allowed_paths=allowed_paths,
        )
        implementer_handoff = await self.orchestrator.delegate(
            implementer_envelope,
            agent_id=assignments.implementer,
            transport=transport,
        )

        reviewer_envelope = TaskEnvelope(
            task_id=task_id,
            run_id=run_id,
            step_id=f"{run_id}-review",
            role=ResearchAgentRole.REVIEWER,
            objective=(
                "Review the approved plan, candidate diff, and validation "
                "evidence. Return an explicit verdict and findings."
            ),
            input_artifact_ids=(
                *planner_handoff.artifact_ids,
                *implementer_handoff.artifact_ids,
            ),
            expected_artifact_types=(ResearchArtifactType.REPORT,),
            allowed_paths=allowed_paths,
            read_only=True,
        )
        reviewer_handoff = await self.orchestrator.delegate(
            reviewer_envelope,
            agent_id=assignments.reviewer,
            transport=transport,
        )
        review = review_parser(reviewer_handoff.artifact_ids)
        if review.run_id != run_id or review.step_id != reviewer_envelope.step_id:
            raise RuntimeError("review decision does not match the active run")
        return MultiAgentWorkflowResult(
            planner_handoff=planner_handoff,
            implementer_handoff=implementer_handoff,
            reviewer_handoff=reviewer_handoff,
            review=review,
        )


def strict_review_parser(
    run_id: str,
    step_id: str,
    *,
    verdict: str,
    findings: tuple[str, ...] = (),
    required_changes: tuple[str, ...] = (),
) -> ReviewDecision:
    return ReviewDecision(
        run_id=run_id,
        step_id=step_id,
        verdict=ReviewVerdict(verdict),
        findings=findings,
        required_changes=required_changes,
    )
