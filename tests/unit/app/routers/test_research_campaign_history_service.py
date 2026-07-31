from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import APIRouter
from starlette.requests import Request

from qwenpaw.app.routers.research_campaign_history_service import (
    install_research_campaign_history_service,
)
from qwenpaw.app.routers.research_campaign_service import CampaignApiState


def _state(
    campaign_id: str,
    *,
    owner_agent_id: str = "default",
    owner_user_id: str | None = "kai",
    status: str = "delivered",
    updated_at: str,
) -> CampaignApiState:
    return CampaignApiState(
        campaign_id=campaign_id,
        status=status,
        repository="owner/repository",
        issue_number=7,
        task_type="bug_fix",
        owner_agent_id=owner_agent_id,
        owner_user_id=owner_user_id,
        owner_session_id=None,
        implementer_agent_id="coder",
        reviewer_agent_id="reviewer",
        acceptance_criteria=["fixed"],
        modifiable_files=["src/fix.py"],
        frozen_files=[],
        created_at=updated_at,
        updated_at=updated_at,
    )


def _request() -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/research/campaigns-history",
            "headers": [],
        }
    )


@pytest.mark.asyncio
async def test_history_is_owner_scoped_sorted_and_filterable() -> None:
    now = datetime.now(timezone.utc)
    module = SimpleNamespace(
        router=APIRouter(prefix="/research"),
        _request_owner_identity=lambda request: ("default", "kai", None),
        _campaign_runs={
            "older": _state("older", updated_at="2026-07-30T00:00:00+00:00"),
            "newer": _state("newer", updated_at=now.isoformat()),
            "failed": _state(
                "failed",
                status="failed",
                updated_at="2026-07-30T12:00:00+00:00",
            ),
            "other": _state(
                "other",
                owner_user_id="other-user",
                updated_at="2026-07-31T00:00:00+00:00",
            ),
        },
    )
    install_research_campaign_history_service(module)
    endpoint = next(
        route.endpoint
        for route in module.router.routes
        if route.path == "/research/campaigns-history"
    )

    all_items = await endpoint(_request(), limit=20, status=None)
    delivered = await endpoint(_request(), limit=20, status="delivered")

    assert [item["campaign_id"] for item in all_items["items"]] == [
        "newer",
        "failed",
        "older",
    ]
    assert [item["campaign_id"] for item in delivered["items"]] == [
        "newer",
        "older",
    ]
    assert all_items["total_matching"] == 3
