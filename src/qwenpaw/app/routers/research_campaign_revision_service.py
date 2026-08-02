"""Bounded revision of one existing Campaign branch and Draft PR."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType
from typing import Any

from fastapi import HTTPException, Request
from pydantic import BaseModel, Field

from ...research_ledger.agent_management_transport import (
    AgentManagementTaskTransport,
)
from ...research_ledger.campaign_delivery import CampaignChangeRequestDeliverer
from ...research_ledger.campaign_execution_runner import CampaignSubprocessRunner
from ...research_ledger.collaboration_contracts import (
    ResearchAgentRole,
    TaskEnvelope,
)
from ...research_ledger.episode_package import EpisodeCommand, EpisodePackage
from ...research_ledger.existing_change_request import (
    ExistingChangeRequestIdentity,
    ExistingChangeRequestProvider,
)
from ...research_ledger.host_evidence_reviewer import HostEvidenceCampaignReviewer
from ...research_ledger.issue_campaign import IssueCampaignRequest, IssueCampaignRunner
from ...research_ledger.worktree_campaign import (
    GitWorktreeCampaignExecutor,
    GitWorktreeCampaignPublisher,
)
from . import research_campaign_direct_service as direct_service
from . import research_campaign_runtime as campaign_runtime
from .research_campaign_monitor_service import _monitor_delivery


class ReviseCampaignRequest(BaseModel):
    feedback: str = Field(default="", max_length=10_000)
    monitor_attempts: int = Field(default=10, ge=1, le=40)
    monitor_interval_seconds: float = Field(default=30.0, ge=0, le=3600)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _request_path(research_module: ModuleType, campaign_id: str) -> Path:
    return Path(research_module._campaign_snapshot_root) / f"{campaign_id}.request.json"


def _revision_path(research_module: ModuleType, campaign_id: str) -> Path:
    return Path(research_module._campaign_snapshot_root) / f"{campaign_id}.revisions.json"


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _persist_state(research_module: ModuleType, state: Any) -> None:
    target = Path(research_module._campaign_snapshot_root) / f"{state.campaign_id}.json"
    _atomic_json(target, asdict(state))


def _owned(research_module: ModuleType, campaign_id: str, request: Request) -> Any:
    state = research_module._campaign_runs.get(campaign_id)
    if state is None:
        raise HTTPException(status_code=404, detail="Unknown campaign")
    current_agent, current_user, _ = research_module._request_owner_identity(request)
    if state.owner_agent_id != current_agent or state.owner_user_id != current_user:
        raise HTTPException(status_code=404, detail="Unknown campaign")
    return state


def _emit(research_module: ModuleType, state: Any, phase: str, detail: str) -> None:
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
    _persist_state(research_module, state)
    for queue in tuple(
        research_module._campaign_sse_queues.get(state.campaign_id, ())
    ):
        try:
            queue.put_nowait(event)
        except asyncio.QueueFull:
            continue


def _close(research_module: ModuleType, campaign_id: str) -> None:
    queues = research_module._campaign_sse_queues.pop(campaign_id, ())
    for queue in tuple(queues):
        try:
            queue.put_nowait(None)
        except asyncio.QueueFull:
            pass


def _load_request(research_module: ModuleType, campaign_id: str) -> Any:
    path = _request_path(research_module, campaign_id)
    if not path.is_file():
        raise RuntimeError(
            "Campaign request snapshot is missing; this run predates revision support"
        )
    payload = json.loads(path.read_text(encoding="utf-8"))
    model = getattr(research_module, "RunIssueCampaignRequest", None)
    if model is None:
        raise RuntimeError("direct Campaign request contract is unavailable")
    return model.model_validate(payload)


def _revision_history(research_module: ModuleType, campaign_id: str) -> list[dict[str, Any]]:
    path = _revision_path(research_module, campaign_id)
    if not path.is_file():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return []
    history = payload.get("revisions", []) if isinstance(payload, dict) else []
    return [item for item in history if isinstance(item, dict)]


def _stored_feedback(state: Any) -> str:
    outcome = state.outcome if isinstance(state.outcome, dict) else {}
    lifecycle = outcome.get("delivery_lifecycle")
    payload: dict[str, Any] = {
        "campaign_error": state.error,
        "delivery_lifecycle": lifecycle if isinstance(lifecycle, dict) else {},
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


async def _live_feedback(
    research_module: ModuleType,
    state: Any,
) -> str:
    outcome = state.outcome if isinstance(state.outcome, dict) else {}
    delivery = outcome.get("delivery") if isinstance(outcome, dict) else None
    pr_url = str(delivery.get("url") or "") if isinstance(delivery, dict) else ""
    if not pr_url or shutil.which("gh") is None:
        return _stored_feedback(state)
    try:
        raw = await research_module._run_process(
            [
                "gh",
                "pr",
                "view",
                pr_url,
                "--repo",
                state.repository,
                "--json",
                "headRefOid,reviewDecision,statusCheckRollup,reviews,comments",
            ],
            cwd=Path(state.worktree_path),
            timeout=120,
        )
    except RuntimeError:
        return _stored_feedback(state)
    try:
        live = json.loads(raw)
    except json.JSONDecodeError:
        return _stored_feedback(state)
    return json.dumps(
        {
            "stored": json.loads(_stored_feedback(state)),
            "live_github_feedback": live,
        },
        ensure_ascii=False,
        indent=2,
    )


def _delivery_identity(state: Any) -> tuple[str, int | None, str, str]:
    outcome = state.outcome if isinstance(state.outcome, dict) else {}
    delivery = outcome.get("delivery") if isinstance(outcome, dict) else None
    if not isinstance(delivery, dict):
        raise RuntimeError("Campaign delivery identity is missing")
    url = str(delivery.get("url") or "")
    number_raw = delivery.get("number")
    number = int(number_raw) if number_raw is not None else None
    head = str(delivery.get("head_branch") or state.branch)
    commit_sha = str(delivery.get("commit_sha") or "")
    if not url or not head or not commit_sha:
        raise RuntimeError("Campaign delivery identity is incomplete")
    return url, number, head, commit_sha


async def _validate_revision_worktree(
    research_module: ModuleType,
    state: Any,
    expected_commit: str,
) -> tuple[Path, str]:
    worktree = Path(state.worktree_path).expanduser().resolve()
    if not worktree.is_dir() or not (worktree / ".git").exists():
        raise RuntimeError("Campaign worktree is unavailable for revision")
    branch = (
        await research_module._run_process(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=worktree,
            timeout=30,
        )
    ).strip()
    if branch != state.branch:
        raise RuntimeError("Campaign worktree branch changed before revision")
    head = (
        await research_module._run_process(
            ["git", "rev-parse", "HEAD"],
            cwd=worktree,
            timeout=30,
        )
    ).strip()
    if head != expected_commit:
        raise RuntimeError(
            "Campaign worktree HEAD does not match the delivered PR commit"
        )
    status = await research_module._run_process(
        ["git", "status", "--porcelain"],
        cwd=worktree,
        timeout=30,
    )
    if status.strip():
        raise RuntimeError(
            "Campaign worktree contains uncommitted changes; preserve or clean "
            "them before revising the delivered change"
        )
    return worktree, head


async def _execute_revision(
    research_module: ModuleType,
    state: Any,
    request: ReviseCampaignRequest,
    revision_number: int,
) -> None:
    campaign_id = state.campaign_id
    try:
        body = _load_request(research_module, campaign_id)
        delivery_mode = str(getattr(body, "delivery_mode", "local"))
        url, number, change_request_head, previous_commit = _delivery_identity(state)
        worktree, base_revision = await _validate_revision_worktree(
            research_module,
            state,
            previous_commit,
        )
        live_feedback = await _live_feedback(research_module, state)
        external_feedback = "\n\n".join(
            value for value in (request.feedback.strip(), live_feedback) if value
        )
        revision_run_id = f"{campaign_id}-revision-{revision_number}"
        token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
        evidence = await asyncio.to_thread(
            campaign_runtime._fetch_github_issue,
            state.repository,
            int(state.issue_number),
            token,
            research_module.urlopen,
        )
        prepared = campaign_runtime._prepared_issue(
            evidence,
            tuple(body.modifiable_files),
        )
        transport = AgentManagementTaskTransport(timeout=900)

        async def implementer(
            episode: EpisodePackage,
            attempt: int,
            feedback: str,
            target_worktree: Path,
        ) -> None:
            combined_feedback = "\n\n".join(
                value for value in (external_feedback, feedback) if value
            )
            _emit(
                research_module,
                state,
                "implementing",
                f"Revision {revision_number}, implementer attempt {attempt}",
            )
            envelope = TaskEnvelope(
                task_id=episode.episode_id,
                run_id=episode.run_id,
                step_id=f"{episode.run_id}-implement-{attempt}",
                role=ResearchAgentRole.IMPLEMENTER,
                objective=(
                    "Revise the existing Campaign directly in the absolute "
                    "worktree below. Do not commit, push, create another PR, or "
                    "change the approved scope. The host will re-run every "
                    "validation and an independent review.\n\n"
                    f"Worktree: {target_worktree}\n"
                    f"Goal: {episode.goal}\n"
                    f"Acceptance criteria: {list(episode.acceptance_criteria)}\n"
                    f"Approved paths: {list(episode.modifiable_files)}\n"
                    f"CI/review feedback:\n{combined_feedback}"
                ),
                allowed_paths=episode.modifiable_files,
            )
            await transport.send(
                str(body.implementer_agent_id),
                envelope,
                from_agent=state.owner_agent_id,
                root_session_id=state.owner_session_id,
            )

        executor = GitWorktreeCampaignExecutor(
            worktree,
            implementer,
            research_module._run_process,
            artifact_root=(
                Path(research_module.WORKING_DIR)
                / ".qwenpaw"
                / "research-campaign-artifacts"
            ),
        )
        reviewer = HostEvidenceCampaignReviewer(
            transport,
            str(body.reviewer_agent_id),
            from_agent=state.owner_agent_id,
            root_session_id=state.owner_session_id,
            emit=lambda phase, detail: _emit(
                research_module,
                state,
                phase,
                detail,
            ),
        )
        publisher = GitWorktreeCampaignPublisher(
            worktree,
            state.branch,
            research_module._run_process,
            push=delivery_mode == "draft_pr",
            change_request_head=change_request_head,
        )
        provider = ExistingChangeRequestProvider(
            ExistingChangeRequestIdentity(
                repository=state.repository,
                base_branch=state.base_branch,
                head_branch=change_request_head,
                url=url,
                number=number,
            )
        )
        deliverer = CampaignChangeRequestDeliverer(
            provider,
            publisher,
            base_branch=state.base_branch,
            draft=True,
        )
        campaign_request = IssueCampaignRequest(
            repository=state.repository,
            issue_number=int(state.issue_number),
            run_id=revision_run_id,
            base_revision=base_revision,
            task_type=str(body.task_type),
            workspace=str(worktree),
            acceptance_criteria=tuple(body.acceptance_criteria),
            commands=tuple(
                EpisodeCommand(
                    command_id=str(command.command_id),
                    stage=str(command.stage),
                    argv=tuple(command.argv),
                    cwd=str(command.cwd),
                    timeout_seconds=float(command.timeout_seconds),
                    required=bool(command.required),
                )
                for command in body.commands
            ),
            frozen_files=tuple(body.frozen_files),
            environment={
                "worktree": str(worktree),
                "delivery_mode": delivery_mode,
                "revision_of": campaign_id,
                "revision_number": str(revision_number),
            },
            max_attempts=int(body.max_attempts),
            modifiable_files=tuple(body.modifiable_files),
        )
        _emit(
            research_module,
            state,
            "revising",
            f"Starting bounded revision {revision_number} on the existing branch",
        )
        validation_home = (
            Path(research_module.WORKING_DIR)
            / ".qwenpaw"
            / "research-campaign-homes"
            / revision_run_id
        )
        outcome = await IssueCampaignRunner(
            campaign_runtime._PreparedIssueService(prepared),
            CampaignSubprocessRunner(validation_home),
        ).run(
            campaign_request,
            executor=executor,
            reviewer=reviewer,
            deliverer=deliverer,
        )
        payload = direct_service._outcome_payload(outcome)
        payload["delivery_mode"] = delivery_mode
        payload["revision_of"] = campaign_id
        payload["revision_number"] = revision_number
        lifecycle: dict[str, Any] | None = None
        if (
            delivery_mode == "draft_pr"
            and outcome.status.value == "delivered"
            and outcome.delivery is not None
            and outcome.delivery_receipt is not None
        ):
            lifecycle = await _monitor_delivery(
                research_module,
                repository=state.repository,
                pr_url=outcome.delivery.url,
                commit_sha=outcome.delivery_receipt.publication.commit_sha,
                worktree=worktree,
                attempts=request.monitor_attempts,
                interval_seconds=request.monitor_interval_seconds,
                emit=lambda phase, detail: _emit(
                    research_module,
                    state,
                    phase,
                    detail,
                ),
            )
            payload["delivery_lifecycle"] = lifecycle

        history = _revision_history(research_module, campaign_id)
        record = {
            "revision_number": revision_number,
            "run_id": revision_run_id,
            "status": outcome.status.value,
            "reason": outcome.reason,
            "commit_sha": (
                outcome.delivery_receipt.publication.commit_sha
                if outcome.delivery_receipt is not None
                else ""
            ),
            "feedback": external_feedback,
            "delivery_lifecycle": lifecycle,
            "finished_at": _utc_now(),
        }
        history.append(record)
        _atomic_json(
            _revision_path(research_module, campaign_id),
            {"campaign_id": campaign_id, "revisions": history},
        )
        payload["revision_history"] = history
        state.outcome = payload
        state.updated_at = _utc_now()
        if outcome.status.value != "delivered":
            state.status = outcome.status.value
            state.error = outcome.reason
        elif lifecycle is not None and lifecycle.get("status") == "needs_revision":
            state.status = "needs_revision"
            state.error = str(lifecycle.get("reason") or "revision required")
        else:
            state.status = "delivered"
            state.error = ""
        state.events.append(
            {
                "phase": state.status,
                "detail": (
                    f"revision={revision_number}; "
                    f"commit={record['commit_sha']}; {state.error or 'completed'}"
                ),
                "timestamp": state.updated_at,
                "sequence": len(state.events) + 1,
            }
        )
        _persist_state(research_module, state)
    except asyncio.CancelledError:
        state.status = "cancelled"
        state.error = "campaign_revision_cancelled"
        state.updated_at = _utc_now()
        _persist_state(research_module, state)
        raise
    except Exception as exc:
        state.status = "needs_revision"
        state.error = f"revision_failed:{type(exc).__name__}:{exc}"
        state.updated_at = _utc_now()
        state.events.append(
            {
                "phase": "needs_revision",
                "detail": state.error,
                "timestamp": state.updated_at,
                "sequence": len(state.events) + 1,
            }
        )
        _persist_state(research_module, state)
    finally:
        _close(research_module, campaign_id)


def install_research_campaign_revision_service(
    research_module: ModuleType,
) -> None:
    """Persist direct requests and register bounded same-request revision."""

    if getattr(research_module, "_campaign_revision_service_installed", False):
        return
    execute_campaign = research_module._execute_issue_campaign

    async def execute_with_request_snapshot(
        module: Any,
        campaign_id: str,
        body: Any,
        **kwargs: Any,
    ) -> Any:
        payload = body.model_dump(mode="json")
        _atomic_json(
            _request_path(module, campaign_id),
            {
                "schema_version": 1,
                "campaign_id": campaign_id,
                "request": payload,
            },
        )
        # Keep the request itself at the top level for backward-compatible reads.
        path = _request_path(module, campaign_id)
        _atomic_json(path, payload)
        return await execute_campaign(module, campaign_id, body, **kwargs)

    research_module._execute_issue_campaign = execute_with_request_snapshot

    @research_module.router.post(
        "/campaigns/{campaign_id}/revise",
        status_code=202,
    )
    async def revise_campaign(
        campaign_id: str,
        body: ReviseCampaignRequest,
        request: Request,
    ) -> dict[str, Any]:
        state = _owned(research_module, campaign_id, request)
        task = research_module._campaign_tasks.get(campaign_id)
        if task is not None and not task.done():
            raise HTTPException(status_code=409, detail="Campaign is already running")
        if state.status not in {
            "needs_revision",
            "failed",
            "blocked",
            "delivered",
        }:
            raise HTTPException(
                status_code=409,
                detail=f"Campaign status cannot be revised: {state.status}",
            )
        request_snapshot = _request_path(research_module, campaign_id)
        if not request_snapshot.is_file():
            raise HTTPException(
                status_code=409,
                detail="Campaign request snapshot is unavailable",
            )
        revisions = _revision_history(research_module, campaign_id)
        original = _load_request(research_module, campaign_id)
        if len(revisions) >= int(original.max_attempts):
            raise HTTPException(
                status_code=409,
                detail="Campaign revision budget is exhausted",
            )
        revision_number = len(revisions) + 1
        _emit(
            research_module,
            state,
            "revision_accepted",
            f"Revision {revision_number} accepted for the existing change request",
        )
        task = asyncio.create_task(
            _execute_revision(
                research_module,
                state,
                body,
                revision_number,
            )
        )
        research_module._campaign_tasks[campaign_id] = task
        return {
            "campaign_id": campaign_id,
            "status": "revision_accepted",
            "revision_number": revision_number,
            "status_url": f"/api/research/campaigns/{campaign_id}",
            "stream_url": f"/api/research/campaigns/{campaign_id}/stream",
        }

    research_module.ReviseCampaignRequest = ReviseCampaignRequest
    research_module._campaign_revision_service_installed = True
