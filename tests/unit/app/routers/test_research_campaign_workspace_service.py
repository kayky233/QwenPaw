from __future__ import annotations

from types import ModuleType, SimpleNamespace

import pytest

from qwenpaw.app.routers import research_campaign_workspace_service as service


@pytest.mark.asyncio
async def test_campaign_workspace_uses_implementer_agent(monkeypatch):
    module = ModuleType("campaign_workspace_test")
    module._dialog_runtime_context = {
        "campaign-1": {
            "workspace": SimpleNamespace(workspace_dir="/owner/workspace")
        }
    }

    async def execute(module_arg, campaign_id, body, **kwargs):
        workspace = module_arg._dialog_runtime_context[campaign_id]["workspace"]
        return workspace.workspace_dir

    module._execute_issue_campaign = execute
    monkeypatch.setattr(
        service,
        "list_agents_data",
        lambda: {
            "agents": [
                {
                    "id": "implementer",
                    "workspace_dir": "/implementer/workspace",
                }
            ]
        },
    )
    service.install_research_campaign_workspace_service(module)

    result = await module._execute_issue_campaign(
        module,
        "campaign-1",
        SimpleNamespace(implementer_agent_id="implementer"),
    )

    assert result == "/implementer/workspace"
    context = module._dialog_runtime_context["campaign-1"]
    assert context["campaign_workspace_agent_id"] == "implementer"


@pytest.mark.asyncio
async def test_campaign_workspace_blocks_unknown_implementer(monkeypatch):
    module = ModuleType("campaign_workspace_test")
    module._dialog_runtime_context = {"campaign-1": {}}

    async def execute(module_arg, campaign_id, body, **kwargs):
        raise AssertionError("runtime must not execute")

    module._execute_issue_campaign = execute
    monkeypatch.setattr(
        service,
        "list_agents_data",
        lambda: {"agents": []},
    )
    service.install_research_campaign_workspace_service(module)

    with pytest.raises(RuntimeError, match="implementer agent was not found"):
        await module._execute_issue_campaign(
            module,
            "campaign-1",
            SimpleNamespace(implementer_agent_id="missing"),
        )
