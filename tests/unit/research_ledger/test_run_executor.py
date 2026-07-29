import pytest

from qwenpaw.research_ledger.contracts import (
    ResearchStepContract,
    ResearchStepType,
)
from qwenpaw.research_ledger.run_executor import (
    ResearchRunExecutor,
    ResearchRunPlan,
)
from qwenpaw.research_ledger.step_repository import (
    ResearchArtifactRepository,
    ResearchStepRepository,
)
from qwenpaw.research_ledger.step_runner import ResearchStepRunner


@pytest.mark.asyncio
async def test_run_executor_executes_steps():
    runner = ResearchStepRunner(
        ResearchStepRepository(),
        ResearchArtifactRepository(),
    )
    executor = ResearchRunExecutor(runner)
    step = ResearchStepContract(
        step_id="s1",
        run_id="r1",
        step_type=ResearchStepType.DISCOVERY,
        index=0,
        executor="agent",
    )
    result = await executor.execute(
        ResearchRunPlan("r1", (step,)),
        {},
    )
    assert len(result) == 1
