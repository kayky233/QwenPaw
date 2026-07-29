from __future__ import annotations

import pytest

from qwenpaw.research_ledger.campaign_delivery import (
    CampaignChangeRequestDeliverer,
)
from qwenpaw.research_ledger.candidate_checkpoint import CandidateCheckpoint
from qwenpaw.research_ledger.change_request_delivery import ChangeRequest
from qwenpaw.research_ledger.change_request_providers import (
    GitHubChangeRequestProvider,
    GitLabChangeRequestProvider,
)
from qwenpaw.research_ledger.contracts import (
    ResearchArtifactContract,
    ResearchArtifactType,
)
from qwenpaw.research_ledger.episode_package import (
    EpisodeCommand,
    EpisodeExpectedArtifact,
    EpisodePackage,
)
from qwenpaw.research_ledger.issue_campaign import CampaignCandidate


class GitHubClient:
    def __init__(self):
        self.calls = []

    def create_pull_request(self, **kwargs):
        self.calls.append(kwargs)
        return {
            "html_url": "https://github.com/owner/repo/pull/9",
            "number": 9,
        }


class GitLabClient:
    def __init__(self):
        self.calls = []

    def create_merge_request(self, **kwargs):
        self.calls.append(kwargs)
        return {
            "web_url": "https://gitlab.example/group/repo/-/merge_requests/7",
            "iid": 7,
        }


def _request():
    return ChangeRequest(
        repository="owner/repo",
        base_branch="main",
        head_branch="autoresearch/fix-cache",
        title="fix: cache expiry",
        body="verified change",
        draft=True,
    )


def test_github_provider_maps_normalized_change_request():
    client = GitHubClient()

    result = GitHubChangeRequestProvider(client).create(_request())

    assert result.number == 9
    assert client.calls == [
        {
            "repository": "owner/repo",
            "base": "main",
            "head": "autoresearch/fix-cache",
            "title": "fix: cache expiry",
            "body": "verified change",
            "draft": True,
        }
    ]


def test_gitlab_provider_maps_normalized_change_request():
    client = GitLabClient()

    result = GitLabChangeRequestProvider(client).create(_request())

    assert result.number == 7
    assert client.calls[0]["project"] == "owner/repo"
    assert client.calls[0]["target_branch"] == "main"
    assert client.calls[0]["source_branch"] == "autoresearch/fix-cache"
    assert client.calls[0]["description"] == "verified change"
    assert client.calls[0]["draft"] is True


def test_provider_rejects_same_base_and_head_branch():
    request = _request()
    invalid = ChangeRequest(
        repository=request.repository,
        base_branch="main",
        head_branch="main",
        title=request.title,
        body=request.body,
    )

    with pytest.raises(ValueError, match="must differ"):
        GitHubChangeRequestProvider(GitHubClient()).create(invalid)


def _episode():
    return EpisodePackage(
        episode_id="ep-1",
        run_id="run-1",
        task_type="bug_fix",
        repository="owner/repo",
        issue_number=12,
        goal="Fix cache expiry",
        base_revision="base",
        acceptance_criteria=("expired entries are hidden",),
        modifiable_files=(
            "src/qwenpaw/memory/cache.py",
            "tests/unit/test_cache.py",
        ),
        commands=(
            EpisodeCommand(
                "unit",
                "unit",
                ("pytest", "-q", "tests/unit/test_cache.py"),
            ),
        ),
        expected_artifacts=(
            EpisodeExpectedArtifact(
                ResearchArtifactType.CODE_DIFF,
                "implement",
            ),
            EpisodeExpectedArtifact(
                ResearchArtifactType.TEST_RESULT,
                "test",
            ),
            EpisodeExpectedArtifact(
                ResearchArtifactType.REPORT,
                "review",
            ),
            EpisodeExpectedArtifact(
                ResearchArtifactType.COMMIT,
                "delivery",
            ),
        ),
    )


def _candidate():
    return CampaignCandidate(
        checkpoint=CandidateCheckpoint(
            candidate_id="candidate-1",
            run_id="run-1",
            parent_revision="base",
            tree_revision="tree-1",
            diff_hash="1" * 64,
            verification_passed=True,
            risk_score=0.2,
        ),
        artifacts=(),
    )


def _artifact(artifact_type, step_id, *, metadata=None, verified=True):
    return ResearchArtifactContract(
        artifact_id=f"{step_id}-{artifact_type.value}",
        run_id="run-1",
        step_id=step_id,
        artifact_type=artifact_type,
        path=f"{artifact_type.value}.json",
        content_hash="a" * 64,
        verified=verified,
        metadata=metadata or {},
    )


def _delivery_artifacts():
    return (
        _artifact(
            ResearchArtifactType.CODE_DIFF,
            "run-1-implement",
            metadata={
                "changed_paths": [
                    "src/qwenpaw/memory/cache.py",
                    "tests/unit/test_cache.py",
                ]
            },
        ),
        _artifact(ResearchArtifactType.TEST_RESULT, "run-1-test"),
        _artifact(ResearchArtifactType.REPORT, "run-1-review"),
        _artifact(ResearchArtifactType.COMMIT, "run-1-delivery"),
    )


@pytest.mark.asyncio
async def test_campaign_deliverer_builds_evidence_body_and_draft_pr():
    client = GitHubClient()
    deliverer = CampaignChangeRequestDeliverer(
        GitHubChangeRequestProvider(client),
        base_branch="main",
        head_branch=lambda episode, candidate: (
            f"autoresearch/{candidate.checkpoint.candidate_id}"
        ),
    )

    result = await deliverer.deliver(
        _episode(),
        _candidate(),
        _delivery_artifacts(),
    )

    assert result.number == 9
    call = client.calls[0]
    assert call["draft"] is True
    assert call["head"] == "autoresearch/candidate-1"
    assert "Closes #12" in call["body"]
    assert "Episode digest" in call["body"]
    assert "Automatic merge remains disabled" in call["body"]
    assert "src/qwenpaw/memory/cache.py" in call["body"]


@pytest.mark.asyncio
async def test_campaign_deliverer_blocks_missing_verified_test_result():
    client = GitHubClient()
    deliverer = CampaignChangeRequestDeliverer(
        GitHubChangeRequestProvider(client),
        base_branch="main",
        head_branch="autoresearch/candidate-1",
    )
    artifacts = tuple(
        artifact
        for artifact in _delivery_artifacts()
        if artifact.artifact_type != ResearchArtifactType.TEST_RESULT
    )

    with pytest.raises(RuntimeError, match="test_result"):
        await deliverer.deliver(_episode(), _candidate(), artifacts)

    assert client.calls == []
