from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from qwenpaw.app.routers.research_campaign_service import CampaignApiState
from qwenpaw.app.routers.research_worktree_service import (
    _checkpoint_campaign_worktree,
)


def test_campaign_worktree_identity_is_persisted_before_execution(
    tmp_path: Path,
) -> None:
    state = CampaignApiState(
        campaign_id="run-1",
        status="preparing_worktree",
        repository="upstream/repository",
        issue_number=7,
        task_type="bug_fix",
        owner_agent_id="default",
        owner_user_id=None,
        owner_session_id=None,
        implementer_agent_id="coder",
        reviewer_agent_id="reviewer",
        acceptance_criteria=["fixed"],
        modifiable_files=["src/fix.py"],
        frozen_files=[],
        created_at="2026-07-31T00:00:00+00:00",
        updated_at="2026-07-31T00:00:00+00:00",
    )
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    module = SimpleNamespace(
        _campaign_runs={"run-1": state},
        _campaign_snapshot_root=tmp_path / "snapshots",
        _campaign_sse_queues={},
        _dialog_runtime_context={
            "run-1": {
                "base_branch": "main",
            }
        },
    )
    dialog = SimpleNamespace(plan_id="run-1")

    _checkpoint_campaign_worktree(
        module,
        dialog,
        (
            worktree,
            "autoresearch/issue-7-run1",
            "upstream/repository",
            "fork-owner/repository",
        ),
    )

    assert state.worktree_path == str(worktree)
    assert state.branch == "autoresearch/issue-7-run1"
    assert state.base_branch == "main"
    assert state.events[-1]["phase"] == "worktree_ready"
    snapshot = json.loads(
        (module._campaign_snapshot_root / "run-1.json").read_text(
            encoding="utf-8"
        )
    )
    assert snapshot["worktree_path"] == str(worktree)
    assert snapshot["branch"] == "autoresearch/issue-7-run1"
    assert snapshot["base_branch"] == "main"
