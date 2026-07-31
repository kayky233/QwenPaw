"""Persist Campaign worktree identity immediately after preparation."""

from __future__ import annotations

import asyncio
import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType
from typing import Any


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def install_research_campaign_worktree_checkpoint_service(
    research_module: ModuleType,
) -> None:
    """Checkpoint worktree identity before implementation or validation starts."""

    if getattr(
        research_module,
        "_campaign_worktree_checkpoint_service_installed",
        False,
    ):
        return
    prepare_worktree = research_module._prepare_research_worktree

    def persist(state: Any) -> None:
        root = Path(research_module._campaign_snapshot_root)
        root.mkdir(parents=True, exist_ok=True)
        target = root / f"{state.campaign_id}.json"
        temporary = target.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(
                asdict(state),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
            encoding="utf-8",
        )
        temporary.replace(target)

    async def checkpointed_prepare(dialog: Any):
        result = await prepare_worktree(dialog)
        plan_id = str(getattr(dialog, "plan_id", ""))
        state = research_module._campaign_runs.get(plan_id)
        if state is None:
            return result
        worktree, branch, upstream_repository, push_repository = result
        context = research_module._dialog_runtime_context.get(plan_id, {})
        base_branch = str(context.get("base_branch") or "")
        changed = (
            state.worktree_path != str(worktree)
            or state.branch != branch
            or state.base_branch != base_branch
        )
        state.worktree_path = str(worktree)
        state.branch = branch
        state.base_branch = base_branch
        state.updated_at = _utc_now()
        if changed:
            event = {
                "phase": "worktree_ready",
                "detail": (
                    f"worktree={worktree}; branch={branch}; "
                    f"upstream={upstream_repository}; push={push_repository}; "
                    f"base={base_branch}"
                ),
                "timestamp": state.updated_at,
                "sequence": len(state.events) + 1,
            }
            state.events.append(event)
            for queue in tuple(
                research_module._campaign_sse_queues.get(plan_id, ())
            ):
                try:
                    queue.put_nowait(event)
                except asyncio.QueueFull:
                    continue
        persist(state)
        return result

    research_module._prepare_research_worktree = checkpointed_prepare
    research_module._campaign_worktree_checkpoint_service_installed = True
