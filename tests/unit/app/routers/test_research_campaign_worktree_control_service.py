from __future__ import annotations

import asyncio
import hashlib
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from qwenpaw.app.routers.research_campaign_service import CampaignApiState
from qwenpaw.app.routers.research_campaign_worktree_control_service import (
    _cleanup_worktree,
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


def _repository(tmp_path: Path) -> tuple[Path, Path, str]:
    source = tmp_path / "repository"
    source.mkdir()
    _run(source, "git", "init")
    _run(source, "git", "config", "user.name", "Test User")
    _run(source, "git", "config", "user.email", "test@example.com")
    tracked = source / "tracked.txt"
    tracked.write_text("baseline\n", encoding="utf-8")
    _run(source, "git", "add", "tracked.txt")
    _run(source, "git", "commit", "-m", "baseline")
    branch = "autoresearch/issue-7-cleanup"
    worktree = source / ".qwenpaw" / "worktrees" / "research-cleanup"
    worktree.parent.mkdir(parents=True)
    _run(source, "git", "worktree", "add", "-b", branch, str(worktree), "HEAD")
    return source, worktree, branch


@pytest.mark.asyncio
async def test_cleanup_exports_tracked_and_untracked_recovery(tmp_path: Path) -> None:
    source, worktree, branch = _repository(tmp_path)
    (worktree / "tracked.txt").write_text("changed\n", encoding="utf-8")
    untracked = worktree / "nested" / "new.txt"
    untracked.parent.mkdir()
    untracked.write_text("untracked content\n", encoding="utf-8")
    snapshot_root = tmp_path / "snapshots"
    state = CampaignApiState(
        campaign_id="campaign-1",
        status="needs_revision",
        repository="owner/repository",
        issue_number=7,
        task_type="bug_fix",
        owner_agent_id="default",
        owner_user_id=None,
        owner_session_id=None,
        implementer_agent_id="implementer",
        reviewer_agent_id="reviewer",
        acceptance_criteria=["fix"],
        modifiable_files=["tracked.txt", "nested/new.txt"],
        frozen_files=[],
        worktree_path=str(worktree),
        branch=branch,
        base_branch="main",
        outcome={},
    )
    module = SimpleNamespace(
        _run_process=_run_process,
        _campaign_snapshot_root=snapshot_root,
    )

    result = await _cleanup_worktree(module, state)

    assert result["status"] == "cleaned"
    assert result["remote_branch_deleted"] is False
    assert result["change_request_modified"] is False
    assert not worktree.exists()
    assert _run(source, "git", "branch", "--list", branch).strip() == ""
    recovery = result["recovery"]
    patch = Path(recovery["patch_path"])
    assert "changed" in patch.read_text(encoding="utf-8")
    assert hashlib.sha256(patch.read_bytes()).hexdigest() == recovery["patch_sha256"]
    copied = [
        item
        for item in recovery["untracked"]
        if item.get("path") == "nested/new.txt"
    ]
    assert copied[0]["status"] == "copied"
    backup = Path(copied[0]["backup_path"])
    assert backup.read_text(encoding="utf-8") == "untracked content\n"
    manifest = json.loads(
        Path(recovery["manifest_path"]).read_text(encoding="utf-8")
    )
    assert manifest["had_uncommitted_changes"] is True
    assert state.worktree_path == ""
    assert state.outcome["worktree_cleanup"]["status"] == "cleaned"


@pytest.mark.asyncio
async def test_cleanup_refuses_non_autoresearch_branch(tmp_path: Path) -> None:
    source, worktree, _ = _repository(tmp_path)
    state = CampaignApiState(
        campaign_id="campaign-2",
        status="failed",
        repository="owner/repository",
        issue_number=7,
        task_type="bug_fix",
        owner_agent_id="default",
        owner_user_id=None,
        owner_session_id=None,
        implementer_agent_id="implementer",
        reviewer_agent_id="reviewer",
        acceptance_criteria=["fix"],
        modifiable_files=["tracked.txt"],
        frozen_files=[],
        worktree_path=str(worktree),
        branch="main",
        base_branch="main",
        outcome={},
    )
    module = SimpleNamespace(
        _run_process=_run_process,
        _campaign_snapshot_root=tmp_path / "snapshots",
    )

    with pytest.raises(RuntimeError, match="non-AutoResearch"):
        await _cleanup_worktree(module, state)

    assert worktree.exists()
    assert source.exists()
