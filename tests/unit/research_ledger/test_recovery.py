from qwenpaw.research_ledger.contracts import ResearchStepContract, ResearchStepStatus, ResearchStepType
from qwenpaw.research_ledger.recovery import ResearchRecoveryManager
from qwenpaw.research_ledger.step_repository import ResearchStepRepository


def test_running_step_recovers_to_blocked():
    repo = ResearchStepRepository()
    repo.create(
        ResearchStepContract(
            step_id="s1",
            run_id="r1",
            step_type=ResearchStepType.IMPLEMENT,
            index=1,
            executor="coder",
            status=ResearchStepStatus.RUNNING,
        )
    )
    result = ResearchRecoveryManager(repo).recover_interrupted_steps("r1")
    assert result[0].recovered_status == ResearchStepStatus.BLOCKED
