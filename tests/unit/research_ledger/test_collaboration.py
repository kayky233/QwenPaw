from datetime import datetime, timezone

import pytest

from qwenpaw.research_ledger.collaboration_contracts import (
    ResearchAgentRole,
    TaskEnvelope,
)
from qwenpaw.research_ledger.collaboration_orchestrator import (
    CollaborationContractError,
    CollaborationOrchestrator,
)
from qwenpaw.research_ledger.contracts import (
    ResearchArtifactContract,
    ResearchArtifactType,
)
from qwenpaw.research_ledger.file_lease import (
    FileLeaseConflict,
    FileLeaseRegistry,
)
from qwenpaw.research_ledger.step_repository import ResearchArtifactRepository


def test_reviewer_envelope_must_be_read_only():
    with pytest.raises(ValueError, match="read-only"):
        TaskEnvelope(
            task_id="task",
            run_id="run",
            step_id="review",
            role=ResearchAgentRole.REVIEWER,
            objective="review",
        )


def test_file_lease_rejects_concurrent_writer():
    leases = FileLeaseRegistry()
    leases.acquire(
        ("src/cache.py",),
        owner="agent-a",
        run_id="run",
        step_id="step-a",
    )
    with pytest.raises(FileLeaseConflict):
        leases.acquire(
            ("src/cache.py",),
            owner="agent-b",
            run_id="run",
            step_id="step-b",
        )


@pytest.mark.asyncio
async def test_orchestrator_validates_and_stores_handoff():
    artifacts = ResearchArtifactRepository()
    orchestrator = CollaborationOrchestrator(artifacts)
    envelope = TaskEnvelope(
        task_id="task",
        run_id="run",
        step_id="implement",
        role=ResearchAgentRole.IMPLEMENTER,
        objective="implement cache fix",
        expected_artifact_types=(ResearchArtifactType.CODE_DIFF,),
        allowed_paths=("src/cache.py",),
    )

    async def transport(agent_id, task):
        assert agent_id == "agent-a"
        assert task is envelope
        return [
            ResearchArtifactContract(
                artifact_id="diff",
                run_id="run",
                step_id="implement",
                artifact_type=ResearchArtifactType.CODE_DIFF,
                path="git.diff",
                content_hash="a" * 64,
                verified=True,
            )
        ]

    handoff = await orchestrator.delegate(
        envelope,
        agent_id="agent-a",
        transport=transport,
    )
    assert handoff.artifact_ids == ("diff",)
    assert artifacts.get("diff") is not None
    assert orchestrator.leases.list_for_run("run") == ()


@pytest.mark.asyncio
async def test_orchestrator_rejects_missing_expected_artifact():
    orchestrator = CollaborationOrchestrator(ResearchArtifactRepository())
    envelope = TaskEnvelope(
        task_id="task",
        run_id="run",
        step_id="test",
        role=ResearchAgentRole.TESTER,
        objective="run tests",
        expected_artifact_types=(ResearchArtifactType.TEST_RESULT,),
    )

    async def transport(agent_id, task):
        return []

    with pytest.raises(CollaborationContractError, match="missing expected"):
        await orchestrator.delegate(
            envelope,
            agent_id="tester",
            transport=transport,
        )
