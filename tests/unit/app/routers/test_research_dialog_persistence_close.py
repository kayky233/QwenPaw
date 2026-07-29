from dataclasses import dataclass, field
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from qwenpaw.app.routers.research_dialog_persistence import (
    PersistingDialogStore,
    install_research_dialog_persistence,
)


@dataclass
class _Dialog:
    plan_id: str
    status: str
    goal: str = "Fix issue"
    events: list[dict] = field(default_factory=list)
    revision: int = 1
    content_hash: str = "a" * 64
    owner_agent_id: str = "default"
    owner_user_id: str | None = "kai"
    owner_session_id: str | None = "chat-1"


@pytest.mark.asyncio
async def test_close_flushes_snapshots_and_clears_process_state() -> None:
    base_initialize = AsyncMock()
    base_close = AsyncMock()
    module = SimpleNamespace(
        DialogRunState=_Dialog,
        _dialog_runs=PersistingDialogStore(),
        _dialog_runtime_context={},
        _dialog_emit=lambda *_: None,
        _ledger_repository=None,
        _log=MagicMock(),
        initialize_research_ledger=base_initialize,
        close_research_ledger=base_close,
    )
    install_research_dialog_persistence(module)
    snapshot_store = SimpleNamespace(upsert=AsyncMock())
    module._dialog_snapshot_store = snapshot_store
    dialog = _Dialog(
        plan_id="plan-close",
        status="awaiting_approval",
    )
    module._dialog_runs[dialog.plan_id] = dialog

    await module.close_research_ledger()

    snapshot_store.upsert.assert_awaited()
    base_close.assert_awaited_once()
    assert module._dialog_snapshot_store is None
    assert module._dialog_runs == {}
    assert module._dialog_snapshot_tails == {}
    assert module._dialog_snapshot_errors == {}
