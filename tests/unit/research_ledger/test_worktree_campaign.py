from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from qwenpaw.research_ledger.contracts import ResearchArtifactType
from qwenpaw.research_ledger.episode_package import (
    EpisodeCommand,
    EpisodeExpectedArtifact,
    EpisodePackage,
)
from qwenpaw.research_ledger.worktree_campaign import (
    GitWorktreeCampaignExecutor,
    GitWorktreeCampaignPublisher,
)


async def _git(argv: list[str], *, cwd: Path, timeout: int) -> str:
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
            f"command failed ({completed.returncode}): {' '.join(argv)}\n"
            f"{completed.stdout}\n{completed.stderr}"
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
    repo = tmp_path / "repo"
    repo.mkdir()
    _run(repo, "git", "init")
    _run(repo, "git", "config", "user.name", "Test User")
    _run(repo, "git", "config", "user.email", "test@example.com")
    source = repo / "src" / "cache.py"
    source.parent.mkdir(parents=True)
    source.write_text("VALUE = 1\n", encoding="utf-8")
    _run(repo, "git", "add", "--all")
    _run(repo, "git", "commit", "-m", "baseline")
    return (
        repo,
        _run(repo, "git", "rev-parse", "HEAD"),
        _run(repo, "git", "rev-parse", "--abbrev-ref", "HEAD"),
    )


def _episode(base_revision: str) -> EpisodePackage:
    return EpisodePackage(
        episode_id="run-1-issue-1",
        run_id="run-1",
        task_type="bug_fix",
        repository="owner/repo",
        issue_number=1,
        goal="Fix cache value",
        base_revision=base_revision,
        acceptance_criteria=("cache value is updated",),
        modifiable_files=("src/cache.py",),
        commands=(
            EpisodeCommand(
                command_id="unit",
                stage="unit",
                argv=("python", "-m", "compileall", "src/cache.py"),
            ),
        ),
        expected_artifacts=(
            EpisodeExpectedArtifact(ResearchArtifactType.PLAN, "plan"),
            EpisodeExpectedArtifact(
                ResearchArtifactType.CODE_DIFF,
                "implement",
            ),
            EpisodeExpectedArtifact(
                ResearchArtifactType.TEST_RESULT,
                "test",
            ),
            EpisodeExpectedArtifact(ResearchArtifactType.REPORT, "review"),
            EpisodeExpectedArtifact(ResearchArtifactType.COMMIT, "delivery"),
        ),
    )


@pytest.mark.asyncio
async def test_worktree_executor_collects_real_diff_and_tree(tmp_path):
    repo, base_revision, _ = _repository(tmp_path)

    async def implementer(episode, attempt, feedback, worktree):
        assert episode.run_id == "run-1"
        assert attempt == 1
        assert feedback == ""
        (worktree / "src" / "cache.py").write_text(
            "VALUE = 2\n",
            encoding="utf-8",
        )

    executor = GitWorktreeCampaignExecutor(
        repo,
        implementer,
        _git,
        artifact_root=tmp_path / "artifacts",
    )

    candidate = await executor.execute(_episode(base_revision), 1, "")

    assert candidate.checkpoint.parent_revision == base_revision
    assert candidate.checkpoint.tree_revision != base_revision
    assert len(candidate.checkpoint.diff_hash) == 64
    artifact = candidate.artifacts[0]
    assert artifact.artifact_type == ResearchArtifactType.CODE_DIFF
    assert artifact.content_hash == candidate.checkpoint.diff_hash
    assert artifact.metadata["changed_paths"] == ["src/cache.py"]
    assert Path(artifact.path).read_text(encoding="utf-8")
    assert _run(repo, "git", "diff", "--cached") == ""


@pytest.mark.asyncio
async def test_worktree_publisher_commits_exact_validated_tree(tmp_path):
    repo, base_revision, branch = _repository(tmp_path)

    async def implementer(episode, attempt, feedback, worktree):
        (worktree / "src" / "cache.py").write_text(
            "VALUE = 3\n",
            encoding="utf-8",
        )

    episode = _episode(base_revision)
    candidate = await GitWorktreeCampaignExecutor(
        repo,
        implementer,
        _git,
        artifact_root=tmp_path / "artifacts",
    ).execute(episode, 1, "")
    publisher = GitWorktreeCampaignPublisher(
        repo,
        branch,
        _git,
        push=False,
        change_request_head=f"fork-owner:{branch}",
    )

    publication = await publisher.publish(episode, candidate.checkpoint)

    assert publication.commit_sha == _run(repo, "git", "rev-parse", "HEAD")
    assert publication.commit_sha != base_revision
    assert publication.head_branch == f"fork-owner:{branch}"
    assert publication.artifact.artifact_type == ResearchArtifactType.COMMIT
    assert publication.artifact.metadata["commit_sha"] == publication.commit_sha
    assert publication.artifact.metadata["head_branch"] == (
        f"fork-owner:{branch}"
    )
    assert publication.artifact.metadata["local_branch"] == branch
    assert publication.artifact.metadata["tree_revision"] == (
        candidate.checkpoint.tree_revision
    )
    assert _run(repo, "git", "status", "--porcelain") == ""


@pytest.mark.asyncio
async def test_worktree_publisher_rejects_changes_after_validation(tmp_path):
    repo, base_revision, branch = _repository(tmp_path)

    async def implementer(episode, attempt, feedback, worktree):
        (worktree / "src" / "cache.py").write_text(
            "VALUE = 4\n",
            encoding="utf-8",
        )

    episode = _episode(base_revision)
    candidate = await GitWorktreeCampaignExecutor(
        repo,
        implementer,
        _git,
        artifact_root=tmp_path / "artifacts",
    ).execute(episode, 1, "")
    (repo / "src" / "cache.py").write_text("VALUE = 5\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="changed after validation"):
        await GitWorktreeCampaignPublisher(
            repo,
            branch,
            _git,
            push=False,
        ).publish(episode, candidate.checkpoint)

    assert _run(repo, "git", "rev-parse", "HEAD") == base_revision
