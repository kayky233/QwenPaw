import pytest

from qwenpaw.research_ledger.contracts import (
    ResearchArtifactContract,
    ResearchArtifactType,
    ResearchStepContract,
    ResearchStepStatus,
    ResearchStepType,
)
from qwenpaw.research_ledger.step_repository import (
    ResearchArtifactRepository,
    ResearchStepRepository,
)
from qwenpaw.research_ledger.step_runner import ResearchStepRunner


@pytest.mark.asyncio
async def test_runner_completes_when_required_artifact_exists():
    steps = ResearchStepRepository()
    artifacts = ResearchArtifactRepository()
    runner = ResearchStepRunner(steps, artifacts)

    step = ResearchStepContract(
        step_id="step-1",
        run_id="run-1",
        step_type=ResearchStepType.TEST,
        index=1,
        executor="tester",
    )

    async def execute(_):
        return [
            ResearchArtifactContract(
                artifact_id="artifact-1",
                run_id="run-1",
                step_id="step-1",
                artifact_type=ResearchArtifactType.TEST_RESULT,
                path="test.json",
                content_hash=ResearchArtifactContract.hash_content("ok"),
                verified=True,
            )
        ]

    result = await runner.execute(step, execute)
    assert result.status == ResearchStepStatus.COMPLETED


@pytest.mark.asyncio
async def test_runner_blocks_missing_artifact():
    runner = ResearchStepRunner(
        ResearchStepRepository(),
        ResearchArtifactRepository(),
    )
    step = ResearchStepContract(
        step_id="step-2",
        run_id="run-1",
        step_type=ResearchStepType.TEST,
        index=1,
        executor="tester",
    )

    async def execute(_):
        return []

    result = await runner.execute(step, execute)
    assert result.status == ResearchStepStatus.BLOCKED
