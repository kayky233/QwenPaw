from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from qwenpaw.app.routers.research_campaign_revision_service import (
    _delivery_identity,
    _revision_history,
    _validate_revision_worktree,
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


async def _run_process(argv, *, cwd, timeout):
    del timeout
    return await asyncio.to_thread(_run, Path(cwd), *argv)


def _worktree(tmp_path: Path):
    repository = tmp_path / "repository"
    repository.mkdir()
    _run(repository, "git", "init")
    _run(repository, "git", "config", "user.name", "Test User")
    _run(repository, "git", "config", "user.email", "test@example.com")
    (repository / "value.txt").write_text("one\n", encoding="utf-8")
    _run(repository, "git", "add", "value.txt")
    _run(repository, "git", "commit", "-m", "baseline")
    branch = "autoresearch/issue-7-revise"
    worktree = repository / ".qwenpaw" / "worktrees" / "revise"
    worktree.parent.mkdir(parents=True)
    _run(repository, "git", "worktree", "add", "-b", branch, str(worktree), "HEAD")
    commit = _run(worktree, "git", "rev-parse", "HEAD").strip()
    return repository, worktree, branch, commit


def _state(worktree: Path, branch: str, commit: str):
    return SimpleNamespace(
        worktree_path=str(worktree),
        branch=branch,
        outcome={
            "delivery": {
                "url": "https://github.com/owner/repository/pull/17",
                "number": 17,
                "head_branch": f"owner:{branch}",
                "commit_sha": commit,
            }
        },
    )


def test_delivery_identity_preserves_existing_pr_and_head() -> None:
    state = _state(Path("/tmp/worktree"), "autoresearch/issue-7-revise", "a" * 40)

    assert _delivery_identity(state) == (
        "https://github.com/owner/repository/pull/17",
        17,
        "owner:autoresearch/issue-7-revise",
        "a" * 40,
    )


def test_delivery_identity_requires_verified_commit_and_url() -> None:
    state = SimpleNamespace(branch="autoresearch/issue-7-revise", outcome={})

    with pytest.raises(RuntimeError, match="delivery identity"):
        _delivery_identity(state)


@pytest.mark.asyncio
async def test_revision_requires_exact_branch_head_and_clean_worktree(
    tmp_path: Path,
) -> None:
    _, worktree, branch, commit = _worktree(tmp_path)
    module = SimpleNamespace(_run_process=_run_process)
    state = _state(worktree, branch, commit)

    resolved, base = await _validate_revision_worktree(module, state, commit)

    assert resolved == worktree.resolve()
    assert base == commit

    (worktree / "value.txt").write_text("dirty\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="uncommitted changes"):
        await _validate_revision_worktree(module, state, commit)


@pytest.mark.asyncio
async def test_revision_blocks_head_or_branch_drift(tmp_path: Path) -> None:
    _, worktree, branch, commit = _worktree(tmp_path)
    module = SimpleNamespace(_run_process=_run_process)
    state = _state(worktree, branch, commit)

    with pytest.raises(RuntimeError, match="does not match"):
        await _validate_revision_worktree(module, state, "f" * 40)

    state.branch = "autoresearch/issue-7-other"
    with pytest.raises(RuntimeError, match="branch changed"):
        await _validate_revision_worktree(module, state, commit)


def test_revision_history_ignores_invalid_snapshot(tmp_path: Path) -> None:
    module = SimpleNamespace(_campaign_snapshot_root=tmp_path)
    path = tmp_path / "campaign-1.revisions.json"
    path.write_text("not-json", encoding="utf-8")

    assert _revision_history(module, "campaign-1") == []

    path.write_text(
        json.dumps({"revisions": [{"revision_number": 1}, "invalid"]}),
        encoding="utf-8",
    )
    assert _revision_history(module, "campaign-1") == [
        {"revision_number": 1}
    ]
