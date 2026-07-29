from __future__ import annotations

from dataclasses import dataclass

import pytest

from qwenpaw.research_ledger.candidate_checkpoint import CandidateCheckpoint
from qwenpaw.research_ledger.change_request_delivery import ChangeRequestResult
from qwenpaw.research_ledger.collaboration_contracts import (
    ReviewDecision,
    ReviewVerdict,
)
from qwenpaw.research_ledger.context_pack import RepositoryContextPack
from qwenpaw.research_ledger.contracts import (
    ResearchArtifactContract,
    ResearchArtifactType,
)
from qwenpaw.research_ledger.episode_package import EpisodeCommand
from qwenpaw.research_ledger.execution_runner import (
    CommandRequest,
    CommandResult,
    ExecutionCapabilities,
)
from qwenpaw.research_ledger.issue_campaign import (
    CampaignCandidate,
    CampaignReview,
    IssueCampaignRequest,
    IssueCampaignRunner,
    IssueCampaignStatus,
)
from qwenpaw.research_ledger.issue_context_planner import ContextualIssueSolvePlan
from qwenpaw.research_ledger.issue_fetcher import IssueEvidence
from qwenpaw.research_ledger.issue_solver import IssueSolvePlan, IssueTask
from qwenpaw.research_ledger.issue_solver_service import PreparedIssueSolve
from qwenpaw.research_ledger.repository_analyzer import RepositoryAnalysis


class PreparedIssueService:
    def __init__(self, prepared):
        self.prepared = prepared

    def prepare(self, repository, issue_number):
        assert repository == "owner/repo"
        assert issue_number == 12
        return self.prepared


class QueueExecutionRunner:
    def __init__(self, exit_codes):
        self.exit_codes = list(exit_codes)
        self.requests = []

    @property
    def capabilities(self):
        return ExecutionCapabilities(
            platform="test",
            network_access=False,
            containerized=True,
            writable_workspace=True,
        )

    def run(self, request: CommandRequest):
        self.requests.append(request)
        exit_code = self.exit_codes.pop(0)
        return CommandResult(
            argv=request.argv,
            cwd=request.cwd,
            exit_code=exit_code,
            stdout="1 passed" if exit_code == 0 else "FAILED tests/test_cache.py::test_expiry",
            stderr="" if exit_code == 0 else "AssertionError: expired item returned",
            duration_seconds=0.1,
            runner_name="QueueExecutionRunner",
        )


class CandidateExecutor:
    def __init__(self, *, changed_paths=None, invalid_hash=False):
        self.changed_paths = changed_paths or [
            "src/qwenpaw/memory/cache.py",
            "tests/unit/test_cache.py",
        ]
        self.invalid_hash = invalid_hash
        self.feedback = []

    async def execute(self, episode, attempt, feedback):
        self.feedback.append(feedback)
        parent = episode.base_revision if attempt == 1 else f"tree-{attempt - 1}"
        checkpoint = CandidateCheckpoint(
            candidate_id=f"candidate-{attempt}",
            run_id=episode.run_id,
            parent_revision=parent,
            tree_revision=f"tree-{attempt}",
            diff_hash="bad" if self.invalid_hash else str(attempt) * 64,
            risk_score=0.1,
        )
        diff = ResearchArtifactContract(
            artifact_id=f"diff-{attempt}",
            run_id=episode.run_id,
            step_id=f"{episode.run_id}-implement",
            artifact_type=ResearchArtifactType.CODE_DIFF,
            path="candidate.patch",
            content_hash=str(attempt) * 64,
            verified=True,
            metadata={"changed_paths": list(self.changed_paths)},
        )
        return CampaignCandidate(checkpoint, (diff,))


class SequenceReviewer:
    def __init__(self, verdicts):
        self.verdicts = list(verdicts)

    async def review(self, episode, candidate, artifacts):
        verdict = self.verdicts.pop(0)
        decision = ReviewDecision(
            run_id=episode.run_id,
            step_id=f"{episode.run_id}-review",
            verdict=verdict,
            findings=("expiry behavior still incorrect",)
            if verdict != ReviewVerdict.APPROVE
            else (),
            required_changes=("hide expired entries",)
            if verdict == ReviewVerdict.REQUEST_CHANGES
            else (),
        )
        report = ResearchArtifactContract(
            artifact_id=f"review-{candidate.checkpoint.candidate_id}",
            run_id=episode.run_id,
            step_id=decision.step_id,
            artifact_type=ResearchArtifactType.REPORT,
            path="review.md",
            content_hash="f" * 64,
            verified=True,
            metadata={"verdict": verdict.value},
        )
        return CampaignReview(decision, report)


@dataclass
class RecordingDeliverer:
    calls: int = 0

    async def deliver(self, episode, candidate, artifacts):
        self.calls += 1
        return ChangeRequestResult(
            url="https://github.com/owner/repo/pull/99",
            number=99,
        )


def _prepared():
    issue = IssueTask(
        repository="owner/repo",
        issue_number=12,
        title="Fix cache expiry",
        description="expired entries remain visible",
    )
    context = RepositoryContextPack(
        items=(),
        affected_paths=("src/qwenpaw/memory/cache.py",),
        test_paths=("tests/unit/test_cache.py",),
        estimated_tokens=20,
        truncated=False,
        graph_available=True,
    )
    contextual = ContextualIssueSolvePlan(
        plan=IssueSolvePlan(
            issue=issue,
            analysis="cache expiry localized",
            implementation_steps=("update cache lookup",),
            validation_steps=("run cache tests",),
        ),
        analysis=RepositoryAnalysis(
            anchors=("cache",),
            candidate_nodes=(),
            affected_paths=("src/qwenpaw/memory/cache.py",),
            test_nodes=(),
            graph_available=True,
        ),
        context_pack=context,
    )
    return PreparedIssueSolve(
        evidence=IssueEvidence(
            repository="owner/repo",
            number=12,
            title="Fix cache expiry",
            body="expired entries remain visible",
            state="open",
            labels=("bug",),
        ),
        contextual_plan=contextual,
    )


def _request(max_attempts=3):
    return IssueCampaignRequest(
        repository="owner/repo",
        issue_number=12,
        run_id="run-1",
        base_revision="base",
        task_type="bug_fix",
        workspace=".",
        acceptance_criteria=("expired entries are not returned",),
        commands=(
            EpisodeCommand(
                "unit",
                "unit",
                ("pytest", "-q", "tests/unit/test_cache.py"),
            ),
        ),
        max_attempts=max_attempts,
    )


@pytest.mark.asyncio
async def test_issue_campaign_repairs_then_delivers_verified_candidate():
    executor = CandidateExecutor()
    reviewer = SequenceReviewer(
        [ReviewVerdict.REQUEST_CHANGES, ReviewVerdict.APPROVE]
    )
    deliverer = RecordingDeliverer()
    runner = IssueCampaignRunner(
        PreparedIssueService(_prepared()),
        QueueExecutionRunner([1, 0]),
    )

    outcome = await runner.run(
        _request(),
        executor=executor,
        reviewer=reviewer,
        deliverer=deliverer,
    )

    assert outcome.status == IssueCampaignStatus.DELIVERED
    assert len(outcome.attempts) == 2
    assert outcome.evidence.ready
    assert outcome.delivery.number == 99
    assert deliverer.calls == 1
    assert "Failure signature:" in executor.feedback[1]
    assert outcome.artifacts[-1].artifact_type == ResearchArtifactType.PULL_REQUEST


@pytest.mark.asyncio
async def test_issue_campaign_blocks_out_of_scope_diff_before_delivery():
    deliverer = RecordingDeliverer()
    outcome = await IssueCampaignRunner(
        PreparedIssueService(_prepared()),
        QueueExecutionRunner([0]),
    ).run(
        _request(),
        executor=CandidateExecutor(
            changed_paths=["src/qwenpaw/app/routers/research.py"]
        ),
        reviewer=SequenceReviewer([ReviewVerdict.APPROVE]),
        deliverer=deliverer,
    )

    assert outcome.status == IssueCampaignStatus.BLOCKED
    assert outcome.reason == "episode_evidence_gate_failed"
    assert outcome.evidence.scope_violations == (
        "src/qwenpaw/app/routers/research.py",
    )
    assert deliverer.calls == 0


@pytest.mark.asyncio
async def test_issue_campaign_blocks_invalid_candidate_without_validation():
    execution_runner = QueueExecutionRunner([0])
    outcome = await IssueCampaignRunner(
        PreparedIssueService(_prepared()),
        execution_runner,
    ).run(
        _request(),
        executor=CandidateExecutor(invalid_hash=True),
        reviewer=SequenceReviewer([ReviewVerdict.APPROVE]),
        deliverer=RecordingDeliverer(),
    )

    assert outcome.status == IssueCampaignStatus.BLOCKED
    assert outcome.reason == "candidate_diff_hash_invalid"
    assert execution_runner.requests == []


@pytest.mark.asyncio
async def test_issue_campaign_stops_immediately_on_blocked_review():
    deliverer = RecordingDeliverer()
    outcome = await IssueCampaignRunner(
        PreparedIssueService(_prepared()),
        QueueExecutionRunner([0]),
    ).run(
        _request(),
        executor=CandidateExecutor(),
        reviewer=SequenceReviewer([ReviewVerdict.BLOCKED]),
        deliverer=deliverer,
    )

    assert outcome.status == IssueCampaignStatus.BLOCKED
    assert outcome.reason == "review_blocked"
    assert deliverer.calls == 0


@pytest.mark.asyncio
async def test_issue_campaign_exhausts_bounded_repair_budget():
    deliverer = RecordingDeliverer()
    outcome = await IssueCampaignRunner(
        PreparedIssueService(_prepared()),
        QueueExecutionRunner([1, 1]),
    ).run(
        _request(max_attempts=2),
        executor=CandidateExecutor(),
        reviewer=SequenceReviewer(
            [ReviewVerdict.REQUEST_CHANGES, ReviewVerdict.REQUEST_CHANGES]
        ),
        deliverer=deliverer,
    )

    assert outcome.status == IssueCampaignStatus.NEEDS_REVISION
    assert outcome.reason == "repair_budget_exhausted"
    assert len(outcome.attempts) == 2
    assert deliverer.calls == 0
