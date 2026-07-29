import pytest

from qwenpaw.research_ledger.agent_management_transport import (
    AgentManagementTaskTransport,
)
from qwenpaw.research_ledger.collaboration_contracts import (
    ResearchAgentRole,
    TaskEnvelope,
)
from qwenpaw.research_ledger.collaboration_orchestrator import (
    CollaborationOrchestrator,
)
from qwenpaw.research_ledger.contracts import (
    ResearchArtifactContract,
    ResearchArtifactType,
)
from qwenpaw.research_ledger.multi_agent_workflow import (
    MultiAgentAssignments,
    MultiAgentResearchWorkflow,
    strict_review_parser,
)
from qwenpaw.research_ledger.step_repository import ResearchArtifactRepository


def _artifact(envelope, artifact_type):
    return ResearchArtifactContract(
        artifact_id=f"{envelope.step_id}-{artifact_type.value}",
        run_id=envelope.run_id,
        step_id=envelope.step_id,
        artifact_type=artifact_type,
        path=f"{artifact_type.value}.txt",
        content_hash="a" * 64,
        verified=True,
    )


def test_agent_transport_prompt_preserves_task_constraints():
    envelope = TaskEnvelope(
        task_id="task",
        run_id="run",
        step_id="review",
        role=ResearchAgentRole.REVIEWER,
        objective="review change",
        allowed_paths=("src/cache.py",),
        read_only=True,
    )
    prompt = AgentManagementTaskTransport.build_prompt(envelope)
    assert '"role": "reviewer"' in prompt
    assert '"read_only": true' in prompt
    assert "src/cache.py" in prompt


@pytest.mark.asyncio
async def test_planner_implementer_reviewer_workflow():
    artifacts = ResearchArtifactRepository()
    workflow = MultiAgentResearchWorkflow(
        CollaborationOrchestrator(artifacts),
    )
    seen_roles = []

    async def transport(agent_id, envelope):
        seen_roles.append((agent_id, envelope.role, envelope.read_only))
        artifact_type = {
            ResearchAgentRole.PLANNER: ResearchArtifactType.PLAN,
            ResearchAgentRole.IMPLEMENTER: ResearchArtifactType.CODE_DIFF,
            ResearchAgentRole.REVIEWER: ResearchArtifactType.REPORT,
        }[envelope.role]
        return [_artifact(envelope, artifact_type)]

    result = await workflow.run(
        task_id="task",
        run_id="run",
        objective="fix cache",
        allowed_paths=("src/cache.py",),
        assignments=MultiAgentAssignments(
            planner="planner-agent",
            implementer="coder-agent",
            reviewer="reviewer-agent",
        ),
        transport=transport,
        review_parser=lambda artifact_ids: strict_review_parser(
            "run",
            "run-review",
            verdict="approve",
            findings=(f"reviewed {artifact_ids[0]}",),
        ),
    )

    assert result.review.approved
    assert seen_roles == [
        ("planner-agent", ResearchAgentRole.PLANNER, True),
        ("coder-agent", ResearchAgentRole.IMPLEMENTER, False),
        ("reviewer-agent", ResearchAgentRole.REVIEWER, True),
    ]
