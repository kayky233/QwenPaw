"""Resolve the implementer workspace before Campaign worktree creation."""

from __future__ import annotations

import asyncio
from types import ModuleType, SimpleNamespace
from typing import Any

from ...agents.tools.agent_management import list_agents_data


def install_research_campaign_workspace_service(
    research_module: ModuleType,
) -> None:
    """Wrap the Campaign runtime with implementer workspace resolution."""

    if getattr(
        research_module,
        "_campaign_workspace_service_installed",
        False,
    ):
        return

    execute_campaign = research_module._execute_issue_campaign

    async def execute_with_implementer_workspace(
        module: Any,
        campaign_id: str,
        body: Any,
        **kwargs: Any,
    ):
        data = await asyncio.to_thread(list_agents_data)
        agents = data.get("agents", []) if isinstance(data, dict) else []
        implementer = next(
            (
                item
                for item in agents
                if isinstance(item, dict)
                and str(item.get("id", ""))
                == str(body.implementer_agent_id)
            ),
            None,
        )
        if implementer is None:
            raise RuntimeError(
                "configured implementer agent was not found: "
                f"{body.implementer_agent_id}"
            )
        workspace_dir = (
            implementer.get("workspace_dir")
            or implementer.get("workspace")
            or implementer.get("working_dir")
        )
        if not isinstance(workspace_dir, str) or not workspace_dir.strip():
            raise RuntimeError(
                "implementer agent does not expose a workspace directory: "
                f"{body.implementer_agent_id}"
            )
        context = module._dialog_runtime_context.setdefault(campaign_id, {})
        context["workspace"] = SimpleNamespace(
            workspace_dir=workspace_dir,
        )
        context["campaign_workspace_agent_id"] = str(
            body.implementer_agent_id
        )
        return await execute_campaign(
            module,
            campaign_id,
            body,
            **kwargs,
        )

    research_module._execute_issue_campaign = (
        execute_with_implementer_workspace
    )
    research_module._campaign_workspace_service_installed = True
