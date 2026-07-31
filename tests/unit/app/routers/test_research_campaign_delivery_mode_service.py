from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from qwenpaw.app.routers import research_campaign_delivery_mode_service as service


@pytest.mark.asyncio
async def test_delivery_mode_preserves_existing_draft_pr_runtime() -> None:
    remote = AsyncMock(return_value="remote-result")
    module = SimpleNamespace(_execute_issue_campaign=remote)
    service.install_research_campaign_delivery_mode_service(module)
    body = SimpleNamespace(delivery_mode="draft_pr")

    result = await module._execute_issue_campaign(
        module,
        "campaign-1",
        body,
        owner_agent_id="default",
        owner_session_id=None,
        emit=lambda *_: None,
    )

    assert result == "remote-result"
    remote.assert_awaited_once()


@pytest.mark.asyncio
async def test_local_delivery_resolves_implementer_and_uses_local_runtime(
    monkeypatch,
) -> None:
    remote = AsyncMock()
    module = SimpleNamespace(_execute_issue_campaign=remote)
    resolve = AsyncMock()
    local = AsyncMock(return_value="local-result")
    monkeypatch.setattr(service, "_resolve_implementer_workspace", resolve)
    monkeypatch.setattr(service, "_execute_local_issue_campaign", local)
    service.install_research_campaign_delivery_mode_service(module)
    body = SimpleNamespace(
        delivery_mode="local",
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

    assert result == "local-result"
    resolve.assert_awaited_once_with(module, "campaign-1", "coder")
    local.assert_awaited_once()
    remote.assert_not_awaited()


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
