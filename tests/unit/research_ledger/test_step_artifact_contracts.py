from qwenpaw.research_ledger.contracts import (
    ResearchArtifactContract,
    ResearchArtifactType,
    ResearchStepContract,
    ResearchStepStatus,
    ResearchStepType,
    required_artifacts_for_step,
)
from qwenpaw.research_ledger.step_repository import (
    ResearchArtifactRepository,
    ResearchStepRepository,
)


def test_step_contract_requires_test_artifact():
    assert required_artifacts_for_step(ResearchStepType.TEST) == (
        ResearchArtifactType.TEST_RESULT,
    )


def test_step_repository_updates_status():
    repo = ResearchStepRepository()
    step = ResearchStepContract(
        step_id="s1",
        run_id="r1",
        step_type=ResearchStepType.IMPLEMENT,
        index=1,
        executor="implementer",
    )
    repo.create(step)
    updated = repo.update_status("s1", ResearchStepStatus.COMPLETED)
    assert updated.status == ResearchStepStatus.COMPLETED


def test_artifact_repository_verification_gate():
    repo = ResearchArtifactRepository()
    repo.add(
        ResearchArtifactContract(
            artifact_id="a1",
            run_id="r1",
            step_id="s1",
            artifact_type=ResearchArtifactType.TEST_RESULT,
            path="report.json",
            content_hash=ResearchArtifactContract.hash_content("ok"),
            verified=True,
        )
    )
    assert repo.has_verified_type("s1", ResearchArtifactType.TEST_RESULT)
