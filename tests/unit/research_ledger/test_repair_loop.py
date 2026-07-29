import pytest

from qwenpaw.research_ledger.candidate_checkpoint import CandidateCheckpoint
from qwenpaw.research_ledger.failure_signature import build_failure_signature
from qwenpaw.research_ledger.repair_loop import RepairLoopController


@pytest.mark.asyncio
async def test_repair_loop_stops_on_verified_candidate():
    async def repair(attempt, failure):
        return CandidateCheckpoint(
            candidate_id=f"c{attempt}",
            run_id="r",
            parent_revision="base",
            tree_revision="tree",
            diff_hash="0" * 64,
            diagnosis=failure,
            verification_passed=True,
        )

    async def next_failure(candidate):
        return None

    result = await RepairLoopController().run(
        build_failure_signature("AssertionError"),
        repair,
        next_failure,
    )
    assert result.best_candidate.verification_passed
    assert result.attempts == 1
    assert result.stopped_reason == "verified_candidate"
