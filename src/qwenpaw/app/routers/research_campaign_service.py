"""Router integration and durable state for AutoResearch Issue Campaigns."""

from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType
from typing import Any

from fastapi import HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from .research_campaign_runtime import (
    CampaignRuntimeResult,
    execute_issue_campaign,
)

_TERMINAL_CAMPAIGN_STATUSES = frozenset(
    {"delivered", "needs_revision", "blocked", "failed", "cancelled"}
)


class CampaignCommandRequest(BaseModel):
    command_id: str = Field(min_length=1, max_length=100)
    stage: str = Field(min_length=1, max_length=50)
    argv: list[str] = Field(min_length=1, max_length=100)
    cwd: str = Field(default=".", min_length=1, max_length=1000)
    timeout_seconds: float = Field(default=300.0, gt=0, le=3600)
    required: bool = True


class StartIssueCampaignRequest(BaseModel):
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
    implementer_agent_id: str = Field(min_length=1, max_length=200)
    reviewer_agent_id: str = Field(min_length=1, max_length=200)
    max_attempts: int = Field(default=3, ge=1, le=10)
    session_id: str | None = Field(default=None, min_length=1, max_length=300)


@dataclass
class CampaignApiState:
    campaign_id: str
    status: str
    repository: str
    issue_number: int
    task_type: str
    owner_agent_id: str
    owner_user_id: str | None
    owner_session_id: str | None
    implementer_agent_id: str
    reviewer_agent_id: str
    acceptance_criteria: list[str]
    modifiable_files: list[str]
    frozen_files: list[str]
    events: list[dict[str, Any]] = field(default_factory=list)
    worktree_path: str = ""
    branch: str = ""
    base_branch: str = ""
    outcome: dict[str, Any] | None = None
    error: str = ""
    created_at: str = ""
    updated_at: str = ""


def install_research_campaign_service(research_module: ModuleType) -> None:
    """Register Campaign APIs before the public research router is mounted."""

    if getattr(research_module, "_campaign_service_installed", False):
        return

    campaign_runs: dict[str, CampaignApiState] = {}
    campaign_tasks: dict[str, asyncio.Task[None]] = {}
    campaign_queues: dict[
        str,
        set[asyncio.Queue[dict[str, Any] | None]],
    ] = {}
    snapshot_root = (
        Path(research_module.WORKING_DIR)
        / ".qwenpaw"
        / "research-campaigns"
    )

    def persist(state: CampaignApiState) -> None:
        snapshot_root.mkdir(parents=True, exist_ok=True)
        target = snapshot_root / f"{state.campaign_id}.json"
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

    def restore() -> None:
        if not snapshot_root.is_dir():
            return
        for path in snapshot_root.glob("*.json"):
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                state = CampaignApiState(**raw)
            except (OSError, UnicodeError, json.JSONDecodeError, TypeError):
                continue
            if state.status not in _TERMINAL_CAMPAIGN_STATUSES:
                state.status = "needs_revision"
                state.error = "runtime_interrupted_requires_review"
                state.updated_at = _utc_now()
                state.events.append(
                    {
                        "phase": "needs_revision",
                        "detail": state.error,
                        "timestamp": state.updated_at,
                        "sequence": len(state.events) + 1,
                    }
                )
                persist(state)
            campaign_runs[state.campaign_id] = state

    def payload(state: CampaignApiState) -> dict[str, Any]:
        return asdict(state)

    def owned(campaign_id: str, request: Request) -> CampaignApiState:
        state = campaign_runs.get(campaign_id)
        if state is None:
            raise HTTPException(status_code=404, detail="Unknown campaign")
        current = research_module._request_owner_identity(request)
        if (
            current[0] != state.owner_agent_id
            or current[1] != state.owner_user_id
        ):
            raise HTTPException(status_code=404, detail="Unknown campaign")
        return state

    def emit(campaign_id: str, phase: str, detail: str) -> None:
        state = campaign_runs[campaign_id]
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
        for queue in tuple(campaign_queues.get(campaign_id, ())):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                continue

    def close(campaign_id: str) -> None:
        for queue in tuple(campaign_queues.pop(campaign_id, ())):
            try:
                queue.put_nowait(None)
            except asyncio.QueueFull:
                pass

    async def execute(
        campaign_id: str,
        body: StartIssueCampaignRequest,
    ) -> None:
        state = campaign_runs[campaign_id]
        try:
            result: CampaignRuntimeResult = (
                await research_module._execute_issue_campaign(
                    research_module,
                    campaign_id,
                    body,
                    owner_agent_id=state.owner_agent_id,
                    owner_session_id=state.owner_session_id,
                    emit=lambda phase, detail: emit(
                        campaign_id,
                        phase,
                        detail,
                    ),
                )
            )
            state.worktree_path = result.worktree
            state.branch = result.branch
            state.base_branch = result.base_branch
            state.outcome = _outcome_payload(result.outcome)
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
                    "detail": result.outcome.reason,
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

    @research_module.router.post("/campaigns", status_code=202)
    async def start_issue_campaign(
        body: StartIssueCampaignRequest,
        request: Request,
    ) -> dict[str, Any]:
        if not research_module.unsafe_research_enabled():
            raise HTTPException(
                status_code=503,
                detail=(
                    "Issue Campaign execution is not sandboxed; set "
                    f"{research_module.UNSAFE_RESEARCH_OPT_IN} to opt in locally"
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
        campaign_runs[campaign_id] = state

        manager = getattr(request.app.state, "multi_agent_manager", None)
        workspace = (
            await manager.get_agent(owner_agent_id)
            if manager is not None
            else None
        )
        research_module._dialog_runtime_context[campaign_id] = {
            "workspace": workspace,
            "app_services": getattr(request.app.state, "app_services", None),
            "planning_root": str(Path(research_module.__file__).resolve().parents[4]),
        }
        persist(state)
        campaign_tasks[campaign_id] = asyncio.create_task(
            execute(campaign_id, body)
        )
        return {
            "campaign_id": campaign_id,
            "status": "accepted",
            "status_url": f"/api/research/campaigns/{campaign_id}",
            "stream_url": f"/api/research/campaigns/{campaign_id}/stream",
        }

    @research_module.router.get("/campaigns/{campaign_id}")
    async def get_issue_campaign(
        campaign_id: str,
        request: Request,
    ) -> dict[str, Any]:
        return payload(owned(campaign_id, request))

    @research_module.router.post("/campaigns/{campaign_id}/cancel")
    async def cancel_issue_campaign(
        campaign_id: str,
        request: Request,
    ) -> dict[str, Any]:
        state = owned(campaign_id, request)
        if state.status in _TERMINAL_CAMPAIGN_STATUSES:
            return payload(state)
        task = campaign_tasks.get(campaign_id)
        if task is not None:
            task.cancel()
        state.status = "cancelled"
        state.error = "campaign_cancelled"
        state.updated_at = _utc_now()
        persist(state)
        close(campaign_id)
        return payload(state)

    @research_module.router.get("/campaigns/{campaign_id}/stream")
    async def stream_issue_campaign(
        campaign_id: str,
        request: Request,
    ) -> StreamingResponse:
        state = owned(campaign_id, request)
        queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue(
            maxsize=256
        )
        campaign_queues.setdefault(campaign_id, set()).add(queue)

        async def event_generator():
            try:
                for event in state.events:
                    yield "data: " + json.dumps(
                        {"type": "event", **event},
                        ensure_ascii=False,
                    ) + "\n\n"
                if state.status in _TERMINAL_CAMPAIGN_STATUSES:
                    yield "data: " + json.dumps(
                        {
                            "type": "final",
                            "status": state.status,
                            "payload": payload(state),
                        },
                        ensure_ascii=False,
                    ) + "\n\n"
                    return
                while True:
                    item = await queue.get()
                    if item is None:
                        latest = campaign_runs[campaign_id]
                        yield "data: " + json.dumps(
                            {
                                "type": "final",
                                "status": latest.status,
                                "payload": payload(latest),
                            },
                            ensure_ascii=False,
                        ) + "\n\n"
                        return
                    yield "data: " + json.dumps(
                        {"type": "event", **item},
                        ensure_ascii=False,
                    ) + "\n\n"
            except asyncio.CancelledError:
                return
            finally:
                queues = campaign_queues.get(campaign_id)
                if queues is not None:
                    queues.discard(queue)
                    if not queues:
                        campaign_queues.pop(campaign_id, None)

        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    restore()
    research_module._campaign_runs = campaign_runs
    research_module._campaign_tasks = campaign_tasks
    research_module._campaign_sse_queues = campaign_queues
    research_module._campaign_snapshot_root = snapshot_root
    research_module._execute_issue_campaign = execute_issue_campaign
    research_module._campaign_service_installed = True


def _outcome_payload(outcome: Any) -> dict[str, Any]:
    return {
        "status": outcome.status.value,
        "reason": outcome.reason,
        "episode_id": outcome.episode.episode_id,
        "episode_digest": outcome.episode.digest(),
        "attempts": [
            {
                "attempt": attempt.attempt,
                "candidate_id": attempt.candidate.checkpoint.candidate_id,
                "tree_revision": attempt.candidate.checkpoint.tree_revision,
                "verification_passed": (
                    attempt.candidate.checkpoint.verification_passed
                ),
                "review_verdict": attempt.review.decision.verdict.value,
                "failure_fingerprint": (
                    attempt.failure.fingerprint
                    if attempt.failure is not None
                    else None
                ),
            }
            for attempt in outcome.attempts
        ],
        "artifacts": [
            {
                "artifact_id": artifact.artifact_id,
                "step_id": artifact.step_id,
                "artifact_type": artifact.artifact_type.value,
                "path": artifact.path,
                "content_hash": artifact.content_hash,
                "verified": artifact.verified,
                "metadata": artifact.metadata,
            }
            for artifact in outcome.artifacts
        ],
        "evidence": (
            {
                "ready": outcome.evidence.ready,
                "missing_artifacts": list(
                    outcome.evidence.missing_artifacts
                ),
                "invalid_artifacts": list(
                    outcome.evidence.invalid_artifacts
                ),
                "scope_violations": list(
                    outcome.evidence.scope_violations
                ),
            }
            if outcome.evidence is not None
            else None
        ),
        "delivery": (
            {
                "url": outcome.delivery.url,
                "number": outcome.delivery.number,
                "commit_sha": (
                    outcome.delivery_receipt.publication.commit_sha
                    if outcome.delivery_receipt is not None
                    else None
                ),
                "head_branch": (
                    outcome.delivery_receipt.publication.head_branch
                    if outcome.delivery_receipt is not None
                    else None
                ),
            }
            if outcome.delivery is not None
            else None
        ),
    }


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
