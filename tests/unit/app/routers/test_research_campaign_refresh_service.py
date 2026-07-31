from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import APIRouter, HTTPException
from starlette.requests import Request

from qwenpaw.app.routers import research_campaign_refresh_service as service
from qwenpaw.app.routers.research_campaign_service import CampaignApiState


def _state(
    tmp_path: Path,
    *,
    delivery_mode: str = "draft_pr",
    status: str = "delivered",
    error: str = "",
    lifecycle: dict | None = None,
) -> CampaignApiState:
    worktree = tmp_path / "worktree"
    worktree.mkdir(exist_ok=True)
    outcome = {
        "delivery_mode": delivery_mode,
        "delivery": {
            "url": "https://github.com/owner/repository/pull/9",
            "commit_sha": "a" * 40,
        },
    }
    if lifecycle is not None:
        outcome["delivery_lifecycle"] = lifecycle
    return CampaignApiState(
        campaign_id="run-1",
        status=status,
        repository="owner/repository",
        issue_number=7,
        task_type="bug_fix",
        owner_agent_id="default",
        owner_user_id="kai",
        owner_session_id=None,
        implementer_agent_id="coder",
        reviewer_agent_id="reviewer",
        acceptance_criteria=["fixed"],
        modifiable_files=["src/fix.py"],
        frozen_files=[],
        worktree_path=str(worktree),
        branch="autoresearch/issue-7-run1",
        base_branch="main",
        outcome=outcome,
        error=error,
        created_at="2026-07-31T00:00:00+00:00",
        updated_at="2026-07-31T00:00:00+00:00",
    )


def _module(tmp_path: Path, state: CampaignApiState) -> SimpleNamespace:
    return SimpleNamespace(
        router=APIRouter(prefix="/research"),
        _campaign_runs={state.campaign_id: state},
        _campaign_sse_queues={},
        _campaign_snapshot_root=tmp_path / "snapshots",
        _request_owner_identity=lambda request: ("default", "kai", None),
        _run_process=None,
    )


def _endpoint(module):
    return next(
        route.endpoint
        for route in module.router.routes
        if route.path == "/research/campaigns/{campaign_id}/refresh-delivery"
    )


def _request() -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/research/campaigns/run-1/refresh-delivery",
            "headers": [],
        }
    )


@pytest.mark.asyncio
async def test_refresh_updates_draft_pr_lifecycle(tmp_path: Path, monkeypatch) -> None:
    state = _state(tmp_path)
    module = _module(tmp_path, state)

    async def monitor(*args, **kwargs):
        emit = kwargs.get("emit")
        if emit:
            emit("review_waiting", "CI passed; review required")
        return {
            "status": "review_waiting",
            "reason": "CI passed; review required",
            "automatic_merge": False,
            "attempts": [{"attempt": 1, "status": "review_waiting"}],
        }

    monkeypatch.setattr(service, "_monitor_delivery", monitor)
    service.install_research_campaign_refresh_service(module)

    payload = await _endpoint(module)("run-1", _request())

    assert payload["status"] == "delivered"
    assert payload["outcome"]["delivery_lifecycle"]["status"] == (
        "review_waiting"
    )
    assert payload["events"][-1]["phase"] == "review_waiting"
    assert (module._campaign_snapshot_root / "run-1.json").is_file()


@pytest.mark.asyncio
async def test_refresh_propagates_ci_failure_to_needs_revision(
    tmp_path: Path,
    monkeypatch,
) -> None:
    state = _state(tmp_path)
    module = _module(tmp_path, state)

    async def monitor(*args, **kwargs):
        return {
            "status": "needs_revision",
            "reason": "CI finished with failed",
            "automatic_merge": False,
            "attempts": [{"attempt": 1, "status": "needs_revision"}],
        }

    monkeypatch.setattr(service, "_monitor_delivery", monitor)
    service.install_research_campaign_refresh_service(module)

    payload = await _endpoint(module)("run-1", _request())

    assert payload["status"] == "needs_revision"
    assert payload["error"] == "CI finished with failed"


@pytest.mark.asyncio
async def test_refresh_recovers_previous_delivery_failure(
    tmp_path: Path,
    monkeypatch,
) -> None:
    previous_reason = "CI finished with failed"
    state = _state(
        tmp_path,
        status="needs_revision",
        error=previous_reason,
        lifecycle={
            "status": "needs_revision",
            "reason": previous_reason,
            "attempts": [],
        },
    )
    module = _module(tmp_path, state)

    async def monitor(*args, **kwargs):
        return {
            "status": "merge_ready",
            "reason": "CI passed and review is approved",
            "automatic_merge": False,
            "attempts": [{"attempt": 1, "status": "merge_ready"}],
        }

    monkeypatch.setattr(service, "_monitor_delivery", monitor)
    service.install_research_campaign_refresh_service(module)

    payload = await _endpoint(module)("run-1", _request())

    assert payload["status"] == "delivered"
    assert payload["error"] == ""
    assert payload["outcome"]["delivery_lifecycle"]["status"] == "merge_ready"


@pytest.mark.asyncio
async def test_refresh_rejects_local_delivery(tmp_path: Path) -> None:
    state = _state(tmp_path, delivery_mode="local")
    module = _module(tmp_path, state)
    service.install_research_campaign_refresh_service(module)

    with pytest.raises(HTTPException) as exc_info:
        await _endpoint(module)("run-1", _request())

    assert exc_info.value.status_code == 409


@pytest.mark.asyncio
async def test_refresh_hides_other_owner_campaign(tmp_path: Path) -> None:
    state = _state(tmp_path)
    module = _module(tmp_path, state)
    module._request_owner_identity = lambda request: ("default", "other", None)
    service.install_research_campaign_refresh_service(module)

    with pytest.raises(HTTPException) as exc_info:
        await _endpoint(module)("run-1", _request())

    assert exc_info.value.status_code == 404
