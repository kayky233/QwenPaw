"""Refresh CI and review evidence for an already delivered Draft PR Campaign."""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType
from typing import Any

from fastapi import HTTPException, Request

from .research_campaign_monitor_service import _monitor_delivery


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def install_research_campaign_refresh_service(
    research_module: ModuleType,
) -> None:
    """Register a read-and-refresh delivery endpoint without write actions."""

    if getattr(research_module, "_campaign_refresh_service_installed", False):
        return

    def owned(campaign_id: str, request: Request):
        state = research_module._campaign_runs.get(campaign_id)
        if state is None:
            raise HTTPException(status_code=404, detail="Unknown campaign")
        current_agent, current_user, _ = (
            research_module._request_owner_identity(request)
        )
        if (
            state.owner_agent_id != current_agent
            or state.owner_user_id != current_user
        ):
            raise HTTPException(status_code=404, detail="Unknown campaign")
        return state

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

    def emit(state: Any, phase: str, detail: str) -> None:
        now = _utc_now()
        state.updated_at = now
        state.events.append(
            {
                "phase": phase,
                "detail": detail,
                "timestamp": now,
                "sequence": len(state.events) + 1,
            }
        )
        persist(state)
        for queue in tuple(
            research_module._campaign_sse_queues.get(state.campaign_id, ())
        ):
            try:
                queue.put_nowait(state.events[-1])
            except Exception:
                continue

    @research_module.router.post(
        "/campaigns/{campaign_id}/refresh-delivery"
    )
    async def refresh_campaign_delivery(
        campaign_id: str,
        request: Request,
    ) -> dict[str, Any]:
        state = owned(campaign_id, request)
        outcome = state.outcome
        if not isinstance(outcome, dict):
            raise HTTPException(
                status_code=409,
                detail="Campaign outcome is not available",
            )
        if outcome.get("delivery_mode") != "draft_pr":
            raise HTTPException(
                status_code=409,
                detail="Only Draft PR Campaign delivery can be refreshed",
            )
        delivery = outcome.get("delivery")
        if not isinstance(delivery, dict):
            raise HTTPException(
                status_code=409,
                detail="Campaign Draft PR delivery is missing",
            )
        pr_url = str(delivery.get("url") or "")
        commit_sha = str(delivery.get("commit_sha") or "")
        worktree = Path(state.worktree_path).expanduser().resolve()
        if not pr_url or not commit_sha:
            raise HTTPException(
                status_code=409,
                detail="Campaign delivery identity is incomplete",
            )
        if not worktree.is_dir():
            raise HTTPException(
                status_code=409,
                detail="Campaign worktree is unavailable for refresh",
            )

        lifecycle = await _monitor_delivery(
            research_module,
            repository=state.repository,
            pr_url=pr_url,
            commit_sha=commit_sha,
            worktree=worktree,
            attempts=1,
            interval_seconds=0,
            emit=lambda phase, detail: emit(state, phase, detail),
        )
        outcome["delivery_lifecycle"] = lifecycle
        state.updated_at = _utc_now()
        if lifecycle["status"] == "needs_revision":
            state.status = "needs_revision"
            state.error = str(lifecycle["reason"])
        elif state.status == "needs_revision" and state.error == str(
            lifecycle.get("reason", "")
        ):
            state.status = "delivered"
            state.error = ""
        persist(state)
        return asdict(state)

    research_module._campaign_refresh_service_installed = True
