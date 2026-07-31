"""Direct-use Campaign API built on the durable Campaign runtime."""

from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType
from typing import Any, Literal

from fastapi import HTTPException, Request
from pydantic import BaseModel, Field

from ...agents.tools.agent_management import list_agents_data
from .research_campaign_service import (
    CampaignApiState,
    CampaignCommandRequest,
    _outcome_payload,
)


class RunIssueCampaignRequest(BaseModel):
    repository: str = Field(
        min_length=3,
        max_length=300,
        pattern=r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$",
    )
    issue_number: int = Field(gt=0)
    task_type: str = Field(
        default="bug_fix",
        pattern=r"^(bug_fix|feature|refactor|performance|research)$",
    )
    acceptance_criteria: list[str] = Field(min_length=1, max_length=50)
    modifiable_files: list[str] = Field(min_length=1, max_length=200)
    frozen_files: list[str] = Field(default_factory=list, max_length=200)
    commands: list[CampaignCommandRequest] = Field(min_length=1, max_length=50)
    implementer_agent_id: str = Field(default="implementer", min_length=1)
    reviewer_agent_id: str = Field(default="reviewer", min_length=1)
    max_attempts: int = Field(default=3, ge=1, le=10)
    delivery_mode: Literal["local", "draft_pr"] = "local"
    session_id: str | None = Field(default=None, min_length=1, max_length=300)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def install_research_campaign_direct_service(
    research_module: ModuleType,
) -> None:
    """Register direct-use Campaign discovery and execution endpoints."""

    if getattr(research_module, "_campaign_direct_service_installed", False):
        return

    def persist(state: CampaignApiState) -> None:
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

    def emit(campaign_id: str, phase: str, detail: str) -> None:
        state = research_module._campaign_runs[campaign_id]
        now = _utc_now()
        state.status = phase
        state.updated_at = now
        event = {
            "phase": phase,
            "detail": detail,
            "timestamp": now,
            "sequence": len(state.events) + 1,
        }
        state.events.append(event)
        persist(state)
        for queue in tuple(
            research_module._campaign_sse_queues.get(campaign_id, ())
        ):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                continue

    def close(campaign_id: str) -> None:
        queues = research_module._campaign_sse_queues.pop(campaign_id, ())
        for queue in tuple(queues):
            try:
                queue.put_nowait(None)
            except asyncio.QueueFull:
                pass

    async def execute(
        campaign_id: str,
        body: RunIssueCampaignRequest,
    ) -> None:
        state = research_module._campaign_runs[campaign_id]
        try:
            result = await research_module._execute_issue_campaign(
                research_module,
                campaign_id,
                body,
                owner_agent_id=state.owner_agent_id,
                owner_session_id=state.owner_session_id,
                emit=lambda phase, detail: emit(campaign_id, phase, detail),
            )
            state.worktree_path = result.worktree
            state.branch = result.branch
            state.base_branch = result.base_branch
            outcome = _outcome_payload(result.outcome)
            outcome["delivery_mode"] = body.delivery_mode
            state.outcome = outcome
            state.status = result.outcome.status.value
            state.error = (
                result.outcome.reason
                if result.outcome.status.value != "delivered"
                else ""
            )
            state.updated_at = _utc_now()
            state.events.append(
                {
                    "phase": state.status,
                    "detail": (
                        f"delivery_mode={body.delivery_mode}; "
                        f"{result.outcome.reason}"
                    ),
                    "timestamp": state.updated_at,
                    "sequence": len(state.events) + 1,
                }
            )
            persist(state)
        except asyncio.CancelledError:
            state.status = "cancelled"
            state.error = "campaign_cancelled"
            state.updated_at = _utc_now()
            persist(state)
            raise
        except Exception as exc:
            state.status = "failed"
            state.error = f"{type(exc).__name__}: {exc}"
            state.updated_at = _utc_now()
            state.events.append(
                {
                    "phase": "failed",
                    "detail": state.error,
                    "timestamp": state.updated_at,
                    "sequence": len(state.events) + 1,
                }
            )
            persist(state)
        finally:
            research_module._dialog_runtime_context.pop(campaign_id, None)
            close(campaign_id)

    @research_module.router.get("/campaigns-info")
    async def campaign_info() -> dict[str, Any]:
        data = await asyncio.to_thread(list_agents_data)
        agents = data.get("agents", []) if isinstance(data, dict) else []
        return {
            "available": True,
            "unsafe_execution_enabled": research_module.unsafe_research_enabled(),
            "delivery_modes": ["local", "draft_pr"],
            "default_delivery_mode": "local",
            "automatic_merge": False,
            "agents": [
                {
                    "id": str(item.get("id", "")),
                    "workspace_dir": str(
                        item.get("workspace_dir")
                        or item.get("workspace")
                        or item.get("working_dir")
                        or ""
                    ),
                }
                for item in agents
                if isinstance(item, dict) and str(item.get("id", "")).strip()
            ],
        }

    @research_module.router.post("/campaigns/run", status_code=202)
    async def run_issue_campaign(
        body: RunIssueCampaignRequest,
        request: Request,
    ) -> dict[str, Any]:
        if not research_module.unsafe_research_enabled():
            raise HTTPException(
                status_code=503,
                detail=(
                    "Issue Campaign execution requires local opt-in; set "
                    f"{research_module.UNSAFE_RESEARCH_OPT_IN}"
                ),
            )
        if body.implementer_agent_id == body.reviewer_agent_id:
            raise HTTPException(
                status_code=422,
                detail="implementer and reviewer agents must be different",
            )

        campaign_id = uuid.uuid4().hex
        now = _utc_now()
        owner_agent_id, owner_user_id, owner_session_id = (
            research_module._request_owner_identity(
                request,
                session_id=body.session_id,
            )
        )
        state = CampaignApiState(
            campaign_id=campaign_id,
            status="accepted",
            repository=body.repository,
            issue_number=body.issue_number,
            task_type=body.task_type,
            owner_agent_id=owner_agent_id,
            owner_user_id=owner_user_id,
            owner_session_id=owner_session_id,
            implementer_agent_id=body.implementer_agent_id,
            reviewer_agent_id=body.reviewer_agent_id,
            acceptance_criteria=list(body.acceptance_criteria),
            modifiable_files=list(body.modifiable_files),
            frozen_files=list(body.frozen_files),
            created_at=now,
            updated_at=now,
        )
        state.events.append(
            {
                "phase": "accepted",
                "detail": f"delivery_mode={body.delivery_mode}",
                "timestamp": now,
                "sequence": 1,
            }
        )
        research_module._campaign_runs[campaign_id] = state

        manager = getattr(request.app.state, "multi_agent_manager", None)
        workspace = (
            await manager.get_agent(owner_agent_id)
            if manager is not None
            else None
        )
        research_module._dialog_runtime_context[campaign_id] = {
            "workspace": workspace,
            "app_services": getattr(request.app.state, "app_services", None),
            "planning_root": str(
                Path(research_module.__file__).resolve().parents[4]
            ),
        }
        persist(state)
        task = asyncio.create_task(execute(campaign_id, body))
        research_module._campaign_tasks[campaign_id] = task
        return {
            "campaign_id": campaign_id,
            "status": "accepted",
            "delivery_mode": body.delivery_mode,
            "status_url": f"/api/research/campaigns/{campaign_id}",
            "stream_url": f"/api/research/campaigns/{campaign_id}/stream",
            "cancel_url": f"/api/research/campaigns/{campaign_id}/cancel",
        }

    research_module.RunIssueCampaignRequest = RunIssueCampaignRequest
    research_module._campaign_direct_service_installed = True
