from __future__ import annotations

from pathlib import Path

import pytest

from qwenpaw.research_ledger.change_request_delivery import (
    ChangeRequest,
    ChangeRequestResult,
)
from qwenpaw.research_ledger.episode_package import (
    EpisodeCommand,
    EpisodeExpectedArtifact,
    EpisodePackage,
)
from qwenpaw.research_ledger.contracts import ResearchArtifactType
from qwenpaw.research_ledger.remote_campaign_e2e import (
    REMOTE_CONFIRMATION,
    RemoteE2EPolicy,
    RemoteIssueEvidence,
    RepositoryBoundDraftProvider if False else RemoteIssueEvidence,
    _monitor_attempt,
    _remote_repository,
    _validate_remote_write,
)
from qwenpaw.research_ledger.delivery_lifecycle import (
    CICheck,
    CICheckStatus,
    CIReport,
)
from qwenpaw.research_ledger.github_delivery_monitor import (
    GitHubDeliverySnapshot,
)


def _episode() -> EpisodePackage:
    return EpisodePackage(
        episode_id="episode-1",
        run_id="run-1",
        task_type="bug_fix",
        repository="owner/e2e-repo",
        issue_number=7,
        goal="Fix the dedicated E2E issue",
        base_revision="a" * 40,
        acceptance_criteria=("Focused test passes",),
        modifiable_files=("src/fix.py",),
        commands=(
            EpisodeCommand(
                command_id="unit",
                stage="unit",
                argv=("pytest", "-q", "tests/test_fix.py"),
            ),
        ),
        expected_artifacts=(
            EpisodeExpectedArtifact(ResearchArtifactType.PLAN, "plan"),
        ),
    )


def _issue(*, labels: tuple[str, ...] = ("autoresearch-e2e",)) -> RemoteIssueEvidence:
    return RemoteIssueEvidence(
        repository="owner/e2e-repo",
        number=7,
        state="open",
        labels=labels,
        title="Dedicated E2E issue",
    )


def test_remote_policy_cannot_enable_merge_or_non_draft_delivery() -> None:
    with pytest.raises(ValueError, match="Draft Pull Requests"):
        RemoteE2EPolicy(
            allowed_repositories=("owner/e2e-repo",),
            draft_only=False,
        ).validate()
    with pytest.raises(ValueError, match="automatic merge"):
        RemoteE2EPolicy(
            allowed_repositories=("owner/e2e-repo",),
            automatic_merge=True,
        ).validate()


def test_remote_write_requires_allowlist_label_and_confirmation() -> None:
    policy = RemoteE2EPolicy(allowed_repositories=("owner/e2e-repo",))
    with pytest.raises(RuntimeError, match="missing required label"):
        _validate_remote_write(
            policy=policy,
            episode=_episode(),
            issue=_issue(labels=("bug",)),
            confirmation=REMOTE_CONFIRMATION,
            origin_repository="owner/e2e-repo",
            base_branch="main",
            branch="autoresearch/e2e/run-1",
        )
    with pytest.raises(RuntimeError, match="confirmation mismatch"):
        _validate_remote_write(
            policy=policy,
            episode=_episode(),
            issue=_issue(),
            confirmation="no",
            origin_repository="owner/e2e-repo",
            base_branch="main",
            branch="autoresearch/e2e/run-1",
        )


def test_remote_repository_parser_accepts_supported_github_remotes() -> None:
    assert _remote_repository("https://github.com/owner/e2e-repo.git") == (
        "owner/e2e-repo"
    )
    assert _remote_repository("git@github.com:owner/e2e-repo.git") == (
        "owner/e2e-repo"
    )
    with pytest.raises(RuntimeError, match="github.com"):
        _remote_repository("https://gitlab.com/owner/e2e-repo.git")


def test_monitor_classifies_ci_and_review_without_side_effects() -> None:
    snapshot = GitHubDeliverySnapshot(
        ci_report=CIReport(
            commit_sha="b" * 40,
            checks=(CICheck("unit", CICheckStatus.PASSED),),
        ),
        review_threads=(),
        review_decision="REVIEW_REQUIRED",
    )
    attempt = _monitor_attempt(snapshot, 1)
    assert attempt.status == "review_waiting"
    assert attempt.blockers == ("review_required",)


def test_change_request_contract_remains_draft() -> None:
    request = ChangeRequest(
        repository="owner/e2e-repo",
        base_branch="main",
        head_branch="autoresearch/e2e/run-1",
        title="test",
        body="body",
        draft=True,
    )
    result = ChangeRequestResult(
        url="https://github.com/owner/e2e-repo/pull/9",
        number=9,
    )
    assert request.draft is True
    assert result.number == 9
