from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from qwenpaw.research_ledger.campaign_delivery import (
    CampaignChangeRequestDeliverer,
)
from qwenpaw.research_ledger.change_request_delivery import (
    ChangeRequest,
    ChangeRequestResult,
)
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
from qwenpaw.research_ledger.execution_runner import LocalSubprocessRunner
from qwenpaw.research_ledger.impact_analysis import ImpactSet
from qwenpaw.research_ledger.issue_campaign import (
    CampaignReview,
    IssueCampaignRequest,
    IssueCampaignRunner,
    IssueCampaignStatus,
)
from qwenpaw.research_ledger.issue_context_planner import (
    ContextualIssueSolvePlan,
)
from qwenpaw.research_ledger.issue_fetcher import IssueEvidence
from qwenpaw.research_ledger.issue_solver import IssueSolvePlan, IssueTask
from qwenpaw.research_ledger.issue_solver_service import PreparedIssueSolve
from qwenpaw.research_ledger.repository_analyzer import RepositoryAnalysis
from qwenpaw.research_ledger.worktree_campaign import (
    GitWorktreeCampaignExecutor,
    GitWorktreeCampaignPublisher,
)


async def _git(
    argv: list[str],
    *,
    cwd: Path,
    timeout: int,
) -> str:
    completed = subprocess.run(
        argv,
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            "Git command failed "
            f"({completed.returncode}): {' '.join(argv)}\n"
            f"{completed.stdout}\n{completed.stderr}",
        )
    return completed.stdout


def _run(repo: Path, *argv: str) -> str:
    completed = subprocess.run(
        list(argv),
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    )
    return completed.stdout.strip()


def _repository(tmp_path: Path) -> tuple[Path, str, str]:
    repo = tmp_path / "campaign-repo"
    repo.mkdir()
    _run(repo, "git", "init")
    _run(repo, "git", "config", "user.name", "Campaign Test")
    _run(repo, "git", "config", "user.email", "campaign@example.com")
    source = repo / "src" / "value.py"
    source.parent.mkdir(parents=True)
    source.write_text("VALUE = 1\n", encoding="utf-8")
    _run(repo, "git", "add", "--all")
    _run(repo, "git", "commit", "-m", "baseline")
    return (
        repo,
        _run(repo, "git", "rev-parse", "HEAD"),
        _run(repo, "git", "rev-parse", "--abbrev-ref", "HEAD"),
    )


def _prepared_issue() -> PreparedIssueSolve:
    issue = IssueTask(
        repository="owner/repo",
        issue_number=7,
        title="Update the value",
        description="The value must be two.",
    )
    source_paths = ("src/value.py",)
    context = RepositoryContextPack(
        items=(),
        affected_paths=source_paths,
        test_paths=(),
        estimated_tokens=0,
        truncated=False,
        graph_available=False,
        downgrade_reason="local_e2e_fixture",
    )
    return PreparedIssueSolve(
        evidence=IssueEvidence(
            repository="owner/repo",
            number=7,
            title=issue.title,
            body=issue.description,
            state="open",
            labels=("bug",),
        ),
        contextual_plan=ContextualIssueSolvePlan(
            plan=IssueSolvePlan(
                issue=issue,
                analysis="Local deterministic E2E fixture",
                implementation_steps=("update src/value.py",),
                validation_steps=("verify VALUE equals two",),
            ),
            analysis=RepositoryAnalysis(
                anchors=("VALUE",),
                candidate_nodes=(),
                affected_paths=source_paths,
                test_nodes=(),
                graph_available=False,
                downgrade_reason="local_e2e_fixture",
            ),
            context_pack=context,
            impact_set=ImpactSet(
                source_paths=source_paths,
                test_paths=(),
                symbols=("VALUE",),
                reasons=("local_e2e_fixture",),
            ),
            selected_tests=(),
        ),
    )


class _PreparedIssueService:
    def prepare(
        self,
        repository: str,
        issue_number: int,
    ) -> PreparedIssueSolve:
        assert repository == "owner/repo"
        assert issue_number == 7
        return _prepared_issue()


class _ApprovingReviewer:
    async def review(
        self,
        episode,
        candidate,
        artifacts,
    ) -> CampaignReview:
        assert candidate.checkpoint.verification_passed is True
        assert any(
            item.artifact_type == ResearchArtifactType.TEST_RESULT
            and item.verified
            for item in artifacts
        )
        decision = ReviewDecision(
            run_id=episode.run_id,
            step_id=f"{episode.run_id}-review",
            verdict=ReviewVerdict.APPROVE,
        )
        report_text = "Local E2E reviewer approved the verified candidate."
        report = ResearchArtifactContract(
            artifact_id=f"{candidate.checkpoint.candidate_id}-review",
            run_id=episode.run_id,
            step_id=decision.step_id,
            artifact_type=ResearchArtifactType.REPORT,
            path="memory://local-e2e-review.md",
            content_hash=ResearchArtifactContract.hash_content(report_text),
            verified=True,
            metadata={"verdict": decision.verdict.value},
        )
        return CampaignReview(decision, report)


class _RecordingChangeRequestProvider:
    def __init__(self) -> None:
        self.requests: list[ChangeRequest] = []

    def create(self, request: ChangeRequest) -> ChangeRequestResult:
        self.requests.append(request)
        return ChangeRequestResult(
            url="https://example.invalid/owner/repo/pull/1",
            number=1,
        )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_local_issue_campaign_runs_two_stage_delivery_without_network(
    tmp_path: Path,
) -> None:
    repo, base_revision, branch = _repository(tmp_path)

    async def implementer(episode, attempt, feedback, worktree):
        assert episode.run_id == "local-e2e"
        assert attempt == 1
        assert feedback == ""
        (worktree / "src" / "value.py").write_text(
            "VALUE = 2\n",
            encoding="utf-8",
        )

    executor = GitWorktreeCampaignExecutor(
        repo,
        implementer,
        _git,
        artifact_root=tmp_path / "artifacts",
    )
    publisher = GitWorktreeCampaignPublisher(
        repo,
        branch,
        _git,
        push=False,
    )
    provider = _RecordingChangeRequestProvider()
    deliverer = CampaignChangeRequestDeliverer(
        provider,
        publisher,
        base_branch=branch,
        draft=True,
    )
    validation_code = (
        "from pathlib import Path; "
        "assert Path('src/value.py').read_text(encoding='utf-8') "
        "== 'VALUE = 2\\n'"
    )
    request = IssueCampaignRequest(
        repository="owner/repo",
        issue_number=7,
        run_id="local-e2e",
        base_revision=base_revision,
        task_type="bug_fix",
        workspace=str(repo),
        acceptance_criteria=("VALUE equals two",),
        commands=(
            EpisodeCommand(
                command_id="focused",
                stage="unit",
                argv=(sys.executable, "-c", validation_code),
                cwd=".",
                timeout_seconds=30,
            ),
        ),
        max_attempts=1,
        modifiable_files=("src/value.py",),
    )

    outcome = await IssueCampaignRunner(
        _PreparedIssueService(),
        LocalSubprocessRunner(),
    ).run(
        request,
        executor=executor,
        reviewer=_ApprovingReviewer(),
        deliverer=deliverer,
    )

    assert outcome.status == IssueCampaignStatus.DELIVERED
    assert outcome.reason == "verified_committed_and_delivered"
    assert outcome.evidence is not None
    assert outcome.evidence.ready is True
    assert outcome.delivery is not None
    assert outcome.delivery.number == 1
    assert outcome.delivery_receipt is not None
    commit_sha = outcome.delivery_receipt.publication.commit_sha
    assert commit_sha == _run(repo, "git", "rev-parse", "HEAD")
    assert commit_sha != base_revision
    assert provider.requests[0].draft is True
    assert provider.requests[0].head_branch == branch

    artifact_types = {
        artifact.artifact_type for artifact in outcome.artifacts
    }
    assert {
        ResearchArtifactType.PLAN,
        ResearchArtifactType.CODE_DIFF,
        ResearchArtifactType.TEST_RESULT,
        ResearchArtifactType.REPORT,
        ResearchArtifactType.COMMIT,
        ResearchArtifactType.PULL_REQUEST,
    }.issubset(artifact_types)
    assert _run(repo, "git", "status", "--porcelain") == ""
