"""Host-verified Git worktree execution and publication for campaigns."""

from __future__ import annotations

import hashlib
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Protocol

from .campaign_delivery_contract import CampaignPublication
from .candidate_checkpoint import CandidateCheckpoint
from .contracts import ResearchArtifactContract, ResearchArtifactType
from .episode_package import EpisodePackage
from .issue_campaign import CampaignCandidate


class AsyncGitRunner(Protocol):
    async def __call__(
        self,
        argv: list[str],
        *,
        cwd: Path,
        timeout: int,
    ) -> str: ...


CampaignImplementer = Callable[
    [EpisodePackage, int, str, Path],
    Awaitable[None],
]


def _same_revision(expected: str, actual: str) -> bool:
    expected_value = expected.strip().lower()
    actual_value = actual.strip().lower()
    return bool(expected_value) and (
        actual_value == expected_value
        or (
            len(expected_value) >= 7
            and actual_value.startswith(expected_value)
        )
    )


class GitWorktreeCampaignExecutor:
    """Run an implementer, then derive candidate evidence from Git itself."""

    def __init__(
        self,
        worktree: Path,
        implementer: CampaignImplementer,
        git: AsyncGitRunner,
        *,
        artifact_root: Path,
    ) -> None:
        self.worktree = worktree.resolve()
        self.implementer = implementer
        self.git = git
        self.artifact_root = artifact_root.resolve()
        self._last_tree_revision: str | None = None

    async def execute(
        self,
        episode: EpisodePackage,
        attempt: int,
        feedback: str,
    ) -> CampaignCandidate:
        if attempt <= 0:
            raise ValueError("campaign attempt must be positive")
        self._validate_worktree()

        head_revision = (
            await self.git(
                ["git", "rev-parse", "HEAD"],
                cwd=self.worktree,
                timeout=30,
            )
        ).strip()
        if attempt == 1 and not _same_revision(
            episode.base_revision,
            head_revision,
        ):
            raise RuntimeError(
                "worktree HEAD does not match episode base revision: "
                f"{head_revision!r} != {episode.base_revision!r}"
            )

        parent_revision = (
            episode.base_revision
            if attempt == 1
            else self._last_tree_revision
        )
        if not parent_revision:
            raise RuntimeError("campaign repair attempt has no parent tree")

        await self.implementer(
            episode,
            attempt,
            feedback,
            self.worktree,
        )

        await self.git(
            ["git", "add", "--all"],
            cwd=self.worktree,
            timeout=60,
        )
        try:
            changed_raw = await self.git(
                [
                    "git",
                    "diff",
                    "--cached",
                    "--name-only",
                    "-z",
                    "--diff-filter=ACMRDTUXB",
                ],
                cwd=self.worktree,
                timeout=60,
            )
            changed_paths = tuple(
                dict.fromkeys(
                    value.replace("\\", "/")
                    for value in changed_raw.split("\0")
                    if value
                )
            )
            if not changed_paths:
                raise RuntimeError("implementer produced no Git changes")

            diff = await self.git(
                [
                    "git",
                    "diff",
                    "--cached",
                    "--binary",
                    "--no-ext-diff",
                    "--full-index",
                ],
                cwd=self.worktree,
                timeout=120,
            )
            if not diff:
                raise RuntimeError("Git returned an empty candidate diff")

            tree_revision = (
                await self.git(
                    ["git", "write-tree"],
                    cwd=self.worktree,
                    timeout=30,
                )
            ).strip()
            if not tree_revision:
                raise RuntimeError("Git did not return a candidate tree")
        finally:
            await self.git(
                ["git", "reset", "--mixed", "HEAD"],
                cwd=self.worktree,
                timeout=60,
            )

        digest = hashlib.sha256(diff.encode("utf-8")).hexdigest()
        candidate_id = f"{episode.episode_id}-candidate-{attempt}"
        patch_path = self._write_patch(
            episode.run_id,
            candidate_id,
            diff,
        )
        risk_score = min(
            1.0,
            len(changed_paths) * 0.05 + len(diff.encode("utf-8")) / 1_000_000,
        )
        checkpoint = CandidateCheckpoint(
            candidate_id=candidate_id,
            run_id=episode.run_id,
            parent_revision=parent_revision,
            tree_revision=tree_revision,
            diff_hash=digest,
            risk_score=risk_score,
        )
        artifact = ResearchArtifactContract(
            artifact_id=f"{candidate_id}-diff",
            run_id=episode.run_id,
            step_id=f"{episode.run_id}-implement",
            artifact_type=ResearchArtifactType.CODE_DIFF,
            path=str(patch_path),
            content_hash=digest,
            verified=True,
            metadata={
                "attempt": attempt,
                "changed_paths": list(changed_paths),
                "parent_revision": parent_revision,
                "tree_revision": tree_revision,
                "diff_bytes": len(diff.encode("utf-8")),
                "worktree": str(self.worktree),
            },
        )
        self._last_tree_revision = tree_revision
        return CampaignCandidate(checkpoint, (artifact,))

    def _validate_worktree(self) -> None:
        if not self.worktree.is_dir():
            raise RuntimeError(f"campaign worktree is missing: {self.worktree}")
        if not (self.worktree / ".git").exists():
            raise RuntimeError(
                f"campaign workspace is not a Git worktree: {self.worktree}"
            )

    def _write_patch(
        self,
        run_id: str,
        candidate_id: str,
        diff: str,
    ) -> Path:
        target = self.artifact_root / run_id / f"{candidate_id}.patch"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(diff, encoding="utf-8")
        return target


class GitWorktreeCampaignPublisher:
    """Commit the verified candidate tree and optionally push its branch."""

    def __init__(
        self,
        worktree: Path,
        branch: str,
        git: AsyncGitRunner,
        *,
        remote: str = "origin",
        push: bool = True,
    ) -> None:
        if not branch.strip():
            raise ValueError("campaign branch is required")
        self.worktree = worktree.resolve()
        self.branch = branch
        self.git = git
        self.remote = remote
        self.push = push

    async def publish(
        self,
        episode: EpisodePackage,
        checkpoint: CandidateCheckpoint,
    ) -> CampaignPublication:
        if checkpoint.run_id != episode.run_id:
            raise ValueError("candidate run_id does not match episode")

        current_branch = (
            await self.git(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                cwd=self.worktree,
                timeout=30,
            )
        ).strip()
        if current_branch != self.branch:
            raise RuntimeError(
                "campaign worktree branch changed before publication: "
                f"{current_branch!r} != {self.branch!r}"
            )

        await self.git(
            ["git", "add", "--all"],
            cwd=self.worktree,
            timeout=60,
        )
        tree_revision = (
            await self.git(
                ["git", "write-tree"],
                cwd=self.worktree,
                timeout=30,
            )
        ).strip()
        if tree_revision != checkpoint.tree_revision:
            await self.git(
                ["git", "reset", "--mixed", "HEAD"],
                cwd=self.worktree,
                timeout=60,
            )
            raise RuntimeError(
                "candidate tree changed after validation: "
                f"{tree_revision!r} != {checkpoint.tree_revision!r}"
            )

        message = self._commit_message(episode)
        await self.git(
            [
                "git",
                "-c",
                "user.name=QwenPaw AutoResearch",
                "-c",
                "user.email=autoresearch@localhost",
                "commit",
                "-m",
                message,
            ],
            cwd=self.worktree,
            timeout=120,
        )
        commit_sha = (
            await self.git(
                ["git", "rev-parse", "HEAD"],
                cwd=self.worktree,
                timeout=30,
            )
        ).strip()
        if not commit_sha:
            raise RuntimeError("Git did not return the published commit SHA")

        if self.push:
            await self.git(
                [
                    "git",
                    "push",
                    "-u",
                    self.remote,
                    self.branch,
                ],
                cwd=self.worktree,
                timeout=600,
            )

        artifact = ResearchArtifactContract(
            artifact_id=f"{checkpoint.candidate_id}-commit",
            run_id=episode.run_id,
            step_id=f"{episode.run_id}-delivery",
            artifact_type=ResearchArtifactType.COMMIT,
            path=f"git://{commit_sha}",
            content_hash=ResearchArtifactContract.hash_content(commit_sha),
            verified=True,
            metadata={
                "commit_sha": commit_sha,
                "head_branch": self.branch,
                "tree_revision": checkpoint.tree_revision,
                "candidate_id": checkpoint.candidate_id,
                "pushed": self.push,
                "remote": self.remote if self.push else "",
            },
        )
        publication = CampaignPublication(
            commit_sha=commit_sha,
            head_branch=self.branch,
            artifact=artifact,
        )
        publication.validate(episode.run_id)
        return publication

    @staticmethod
    def _commit_message(episode: EpisodePackage) -> str:
        prefix = {
            "bug_fix": "fix",
            "feature": "feat",
            "refactor": "refactor",
            "performance": "perf",
            "research": "research",
        }.get(episode.task_type, "change")
        return f"{prefix}: {episode.goal}"[:240]
