"""Owner-scoped history API for durable Issue Campaign runs."""

from __future__ import annotations

from dataclasses import asdict
from types import ModuleType
from typing import Any

from fastapi import Query, Request


def install_research_campaign_history_service(
    research_module: ModuleType,
) -> None:
    """Expose owner-scoped Campaign history without changing run semantics."""

    if getattr(research_module, "_campaign_history_service_installed", False):
        return

    @research_module.router.get("/campaigns-history")
    async def list_campaign_history(
        request: Request,
        limit: int = Query(default=20, ge=1, le=200),
        status: str | None = Query(default=None, max_length=50),
    ) -> dict[str, Any]:
        owner_agent_id, owner_user_id, _ = (
            research_module._request_owner_identity(request)
        )
        states = [
            item
            for item in research_module._campaign_runs.values()
            if item.owner_agent_id == owner_agent_id
            and item.owner_user_id == owner_user_id
            and (status is None or item.status == status)
        ]
        states.sort(
            key=lambda item: (item.updated_at, item.created_at, item.campaign_id),
            reverse=True,
        )
        selected = states[:limit]
        return {
            "count": len(selected),
            "total_matching": len(states),
            "items": [asdict(item) for item in selected],
        }

    research_module._campaign_history_service_installed = True
