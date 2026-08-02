from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path

import pytest

from qwenpaw.research_ledger.campaign_delivery import (
    CampaignChangeRequestDeliverer,
)
from qwenpaw.research_ledger.change_request_delivery import (
    ChangeRequestResult,
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
from qwenpaw.research_ledger.existing_change_request import (
    ExistingChangeRequestIdentity,
    ExistingChangeRequestProvider,
)
from qwenpaw.research_ledger.worktree_campaign import (
    GitWorktreeCampaignExecutor,
    GitWorktreeCampaignPublisher,
)


class _FirstProvider:
    def __init__(self) -> None:
        self.calls = []

    def create(self, request):
        self.calls.append(request)
        return ChangeRequestResult(
            url="https://github.com/owner/repository/pull/23",
            number=23,
        )


def _run(repository: Path, *argv: str) -> str:
    completed = subprocess.run(
        list(argv),
        cwd=repository,
        capture_output=True,
        text=True,
        check=True,
    )
    return completed.stdout


async def _git(argv, *, cwd, timeout):
    del timeout
    return await asyncio.to_thread(_run, Path(cwd), *argv)


def _repository(tmp_path: Path) -> tuple[Path, str, str]:
    repository = tmp_path / "repository"
    repository.mkdir()
    _run(repository, "git", "init")
    _run(repository, "git", "config", "user.name", "Test User")
    _run(repository, "git", "config", "user.email", "test@example.com")
    (repository / "value.txt").write_text("one\n", encoding="utf-8")
    _run(repository, "git", "add", "value.txt")
    _run(repository, "git", "commit", "-m", "baseline")
    branch = "autoresearch/issue-23-revision"
    _run(repository, "git", "checkout", "-b", branch)
    return repository, branch, _run(repository, "git", "rev-parse", "HEAD").strip()


def _episode(run_id: str, base_revision: str) -> EpisodePackage:
    return EpisodePackage(
        episode_id=f"{run_id}-episode",
        run_id=run_id,
        task_type="bug_fix",
        repository="owner/repository",
        issue_number=23,
        goal="Update the same Draft PR",
        base_revision=base_revision,
        acceptance_criteria=("The verified value advances",),
        modifiable_files=("value.txt",),
        commands=(
            EpisodeCommand(
                command_id="unit",
                stage="unit",
                argv=("python", "-c", "print('ok')"),
            ),
        ),
        expected_artifacts=(
            EpisodeExpectedArtifact(ResearchArtifactType.CODE_DIFF, "implement"),
            EpisodeExpectedArtifact(ResearchArtifactType.TEST_RESULT, "test"),
            EpisodeExpectedArtifact(ResearchArtifactType.REPORT, "review"),
            EpisodeExpectedArtifact(ResearchArtifactType.COMMIT, "delivery"),
        ),
    )


def _test_artifact(run_id: str) -> ResearchArtifactContract:
    metadata = {
        "argv": ["python", "-c", "print('ok')"],
        "cwd": ".",
        "exit_code": 0,
        "stdout": "ok\n",
        "stderr": "",
        "duration_seconds": 0.01,
        "timed_out": False,
    }
    encoded = json.dumps(metadata, sort_keys=True, ensure_ascii=False)
    return ResearchArtifactContract(
        artifact_id=f"{run_id}-test",
        run_id=run_id,
        step_id=f"{run_id}-test",
        artifact_type=ResearchArtifactType.TEST_RESULT,
        path=f"{run_id}-test.json",
        content_hash=ResearchArtifactContract.hash_content(encoded),
        verified=True,
        metadata=metadata,
    )


def _review_artifact(run_id: str) -> ResearchArtifactContract:
    content = '{"verdict":"approve"}'
    return ResearchArtifactContract(
        artifact_id=f"{run_id}-review",
        run_id=run_id,
        step_id=f"{run_id}-review",
        artifact_type=ResearchArtifactType.REPORT,
        path="agent://reviewer/session",
        content_hash=ResearchArtifactContract.hash_content(content),
        verified=True,
        metadata={"verdict": "approve"},
    )


async def _candidate(
    repository: Path,
    episode: EpisodePackage,
    value: str,
    artifact_root: Path,
):
    async def implementer(current, attempt, feedback, worktree):
        del current, attempt, feedback
        (worktree / "value.txt").write_text(value, encoding="utf-8")

    return await GitWorktreeCampaignExecutor(
        repository,
        implementer,
        _git,
        artifact_root=artifact_root,
    ).execute(episode, 1, "")


@pytest.mark.asyncio
async def test_revision_reuses_same_branch_and_change_request(tmp_path: Path) -> None:
    repository, branch, baseline = _repository(tmp_path)
    artifact_root = tmp_path / "artifacts"

    first_episode = _episode("run-first", baseline)
    first_candidate = await _candidate(
        repository,
        first_episode,
        "two\n",
        artifact_root,
    )
    first_provider = _FirstProvider()
    first_receipt = await CampaignChangeRequestDeliverer(
        first_provider,
        GitWorktreeCampaignPublisher(
            repository,
            branch,
            _git,
            push=False,
            change_request_head=f"owner:{branch}",
        ),
        base_branch="main",
        draft=True,
    ).deliver(
        first_episode,
        first_candidate,
        (
            *first_candidate.artifacts,
            _test_artifact(first_episode.run_id),
            _review_artifact(first_episode.run_id),
        ),
    )

    second_episode = _episode(
        "run-second",
        first_receipt.publication.commit_sha,
    )
    second_candidate = await _candidate(
        repository,
        second_episode,
        "three\n",
        artifact_root,
    )
    existing = ExistingChangeRequestProvider(
        ExistingChangeRequestIdentity(
            repository="owner/repository",
            base_branch="main",
            head_branch=f"owner:{branch}",
            url=first_receipt.change_request.url,
            number=first_receipt.change_request.number,
        )
    )
    second_receipt = await CampaignChangeRequestDeliverer(
        existing,
        GitWorktreeCampaignPublisher(
            repository,
            branch,
            _git,
            push=False,
            change_request_head=f"owner:{branch}",
        ),
        base_branch="main",
        draft=True,
    ).deliver(
        second_episode,
        second_candidate,
        (
            *second_candidate.artifacts,
            _test_artifact(second_episode.run_id),
            _review_artifact(second_episode.run_id),
        ),
    )

    assert second_receipt.change_request.url == first_receipt.change_request.url
    assert second_receipt.change_request.number == 23
    assert second_receipt.publication.head_branch == f"owner:{branch}"
    assert second_receipt.publication.commit_sha != (
        first_receipt.publication.commit_sha
    )
    assert _run(repository, "git", "rev-parse", "--abbrev-ref", "HEAD").strip() == branch
    assert _run(repository, "git", "rev-parse", "HEAD").strip() == (
        second_receipt.publication.commit_sha
    )
    assert len(existing.requests) == 1
