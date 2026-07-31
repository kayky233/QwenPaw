from __future__ import annotations

import subprocess
from pathlib import Path

from qwenpaw.research_ledger.campaign_local_control import (
    APPLY_CONFIRMATION,
    CLEANUP_LOCAL_CONFIRMATION,
    apply_local_campaign_patch,
    cleanup_local_campaign,
)


def _run(repository: Path, *argv: str) -> str:
    completed = subprocess.run(
        list(argv),
        cwd=repository,
        capture_output=True,
        text=True,
        check=True,
    )
    return completed.stdout.strip()


def _repository(
    tmp_path: Path,
    *,
    owner: str = "owner",
) -> tuple[Path, str]:
    repository = tmp_path / f"repository-{owner}"
    repository.mkdir()
    _run(repository, "git", "init")
    _run(repository, "git", "config", "user.name", "Test User")
    _run(repository, "git", "config", "user.email", "test@example.com")
    _run(
        repository,
        "git",
        "remote",
        "add",
        "origin",
        f"https://github.com/{owner}/repository.git",
    )
    source = repository / "src" / "fix.py"
    source.parent.mkdir(parents=True)
    source.write_text("VALUE = 1\n", encoding="utf-8")
    _run(repository, "git", "add", "--all")
    _run(repository, "git", "commit", "-m", "baseline")
    return repository, _run(repository, "git", "rev-parse", "HEAD")


def _state(
    tmp_path: Path,
    *,
    parent_revision: str,
    patch_path: Path,
    worktree: Path | None = None,
    branch: str = "autoresearch/issue-7-test",
) -> dict:
    return {
        "campaign_id": "campaign-1",
        "status": "delivered",
        "repository": "owner/repository",
        "modifiable_files": ["src/fix.py"],
        "worktree_path": str(worktree or tmp_path / "unused"),
        "branch": branch,
        "outcome": {
            "delivery_mode": "local",
            "delivery": {
                "url": "local://autoresearch/campaign-1/draft-change-request"
            },
            "artifacts": [
                {
                    "artifact_type": "code_diff",
                    "verified": True,
                    "path": str(patch_path),
                    "metadata": {
                        "parent_revision": parent_revision,
                        "changed_paths": ["src/fix.py"],
                    },
                }
            ],
        },
    }


def _candidate_patch(repository: Path, target: Path) -> None:
    source = repository / "src" / "fix.py"
    source.write_text("VALUE = 2\n", encoding="utf-8")
    target.write_text(
        _run(repository, "git", "diff", "--binary"),
        encoding="utf-8",
    )
    _run(repository, "git", "checkout", "--", "src/fix.py")


def test_apply_local_campaign_stages_verified_patch(tmp_path: Path) -> None:
    repository, head = _repository(tmp_path)
    patch_path = tmp_path / "candidate.patch"
    _candidate_patch(repository, patch_path)

    result = apply_local_campaign_patch(
        _state(tmp_path, parent_revision=head, patch_path=patch_path),
        repository,
        confirmation=APPLY_CONFIRMATION,
    )

    assert result.staged is True
    assert result.committed is False
    assert _run(repository, "git", "diff", "--cached", "--name-only") == (
        "src/fix.py"
    )


def test_apply_local_campaign_accepts_same_name_fork(tmp_path: Path) -> None:
    repository, head = _repository(tmp_path, owner="fork-owner")
    patch_path = tmp_path / "fork-candidate.patch"
    _candidate_patch(repository, patch_path)

    result = apply_local_campaign_patch(
        _state(tmp_path, parent_revision=head, patch_path=patch_path),
        repository,
        confirmation=APPLY_CONFIRMATION,
    )

    assert result.staged is True
    assert _run(repository, "git", "diff", "--cached", "--name-only") == (
        "src/fix.py"
    )


def test_cleanup_removes_only_campaign_worktree_and_branch(tmp_path: Path) -> None:
    repository, head = _repository(tmp_path)
    patch_path = tmp_path / "unused.patch"
    patch_path.write_text("", encoding="utf-8")
    worktree = tmp_path / "campaign-worktree"
    branch = "autoresearch/issue-7-test"
    _run(
        repository,
        "git",
        "worktree",
        "add",
        "-b",
        branch,
        str(worktree),
        head,
    )

    result = cleanup_local_campaign(
        _state(
            tmp_path,
            parent_revision=head,
            patch_path=patch_path,
            worktree=worktree,
            branch=branch,
        ),
        confirmation=CLEANUP_LOCAL_CONFIRMATION,
    )

    assert result.removed is True
    assert not worktree.exists()
    assert _run(repository, "git", "branch", "--list", branch) == ""
