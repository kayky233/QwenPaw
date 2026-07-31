from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from qwenpaw.app.routers import research_campaign_delivery_mode_service as service


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["local", "draft_pr"])
async def test_delivery_modes_resolve_workspace_and_use_unified_runtime(
    mode: str,
    monkeypatch,
) -> None:
    module = SimpleNamespace(_execute_issue_campaign=AsyncMock())
    resolve = AsyncMock()
    execute = AsyncMock(return_value=f"{mode}-result")
    monkeypatch.setattr(service, "_resolve_implementer_workspace", resolve)
    monkeypatch.setattr(
        service,
        "_execute_issue_campaign_with_delivery",
        execute,
    )
    service.install_research_campaign_delivery_mode_service(module)
    body = SimpleNamespace(
        delivery_mode=mode,
        implementer_agent_id="coder",
    )

    result = await module._execute_issue_campaign(
        module,
        "campaign-1",
        body,
        owner_agent_id="default",
        owner_session_id=None,
        emit=lambda *_: None,
    )

    assert result == f"{mode}-result"
    resolve.assert_awaited_once_with(module, "campaign-1", "coder")
    assert execute.await_args.kwargs["delivery_mode"] == mode


@pytest.mark.asyncio
async def test_unknown_delivery_mode_is_rejected() -> None:
    module = SimpleNamespace(_execute_issue_campaign=AsyncMock())
    service.install_research_campaign_delivery_mode_service(module)

    with pytest.raises(ValueError, match="unsupported Campaign delivery mode"):
        await module._execute_issue_campaign(
            module,
            "campaign-1",
            SimpleNamespace(delivery_mode="merge"),
        )


def test_repository_parts_support_upstream_and_fork_identity() -> None:
    assert service._repository_parts("upstream/project") == (
        "upstream",
        "project",
    )
    assert service._repository_parts("fork-owner/project") == (
        "fork-owner",
        "project",
    )
    with pytest.raises(RuntimeError, match="invalid Campaign repository"):
        service._repository_parts("project")
