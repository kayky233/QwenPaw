from qwenpaw.research_ledger.contracts import (
    ResearchArtifactContract,
    ResearchArtifactType,
)
from qwenpaw.research_ledger.delivery_lifecycle import (
    CICheck,
    CICheckStatus,
    CIReport,
    DeliveryStatus,
    MergeReadinessEvaluator,
    ReviewThread,
)
from qwenpaw.research_ledger.step_repository import ResearchArtifactRepository


def _add(repo, step_id, artifact_type):
    repo.add(
        ResearchArtifactContract(
            artifact_id=f"{step_id}-{artifact_type.value}",
            run_id="run",
            step_id=step_id,
            artifact_type=artifact_type,
            path=artifact_type.value,
            content_hash="a" * 64,
            verified=True,
        )
    )


def test_merge_ready_requires_all_evidence_ci_and_review():
    artifacts = ResearchArtifactRepository()
    step_ids = {
        ResearchArtifactType.CODE_DIFF: "implement",
        ResearchArtifactType.TEST_RESULT: "test",
        ResearchArtifactType.REPORT: "review",
        ResearchArtifactType.COMMIT: "delivery",
        ResearchArtifactType.PULL_REQUEST: "delivery",
    }
    for artifact_type, step_id in step_ids.items():
        _add(artifacts, step_id, artifact_type)

    readiness = MergeReadinessEvaluator(artifacts).evaluate(
        artifact_step_ids=step_ids,
        ci_report=CIReport(
            commit_sha="abc",
            checks=(CICheck("tests", CICheckStatus.PASSED),),
        ),
        review_threads=(
            ReviewThread("thread", "reviewer", "looks good", resolved=True),
        ),
    )
    assert readiness.ready
    assert readiness.status == DeliveryStatus.MERGE_READY


def test_failed_ci_blocks_merge():
    readiness = MergeReadinessEvaluator(ResearchArtifactRepository()).evaluate(
        artifact_step_ids={},
        ci_report=CIReport(
            commit_sha="abc",
            checks=(CICheck("tests", CICheckStatus.FAILED),),
        ),
    )
    assert not readiness.ready
    assert readiness.status == DeliveryStatus.CI_FAILED
    assert "ci_failed" in readiness.blockers
