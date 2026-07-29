from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from qwenpaw.app.routers.research_dialog_persistence import (
    PersistingDialogStore,
    dialog_snapshot_payload,
    install_research_dialog_persistence,
    recover_interrupted_dialog_payload,
    serializable_runtime_context,
)
from qwenpaw.app.routers.research_state_machine import (
    InvalidResearchTransition,
)
from qwenpaw.research_ledger.dialog_snapshot import (
    ResearchDialogSnapshot,
    encode_dialog_state,
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
    worktree_path: str = ""
    validation_failure_category: str = ""
    error: str = ""


def _snapshot(
    payload: dict,
) -> ResearchDialogSnapshot:
    encoded, digest = encode_dialog_state(payload)
    return ResearchDialogSnapshot(
        plan_id=payload["plan_id"],
        schema_version=1,
        status=payload["status"],
        revision=payload.get("revision", 0),
        content_hash=payload.get("content_hash", ""),
        state_hash=digest,
        owner_agent_id=payload["owner_agent_id"],
        owner_user_id=payload.get("owner_user_id"),
        owner_session_id=payload.get("owner_session_id"),
        state_json=encoded,
    )


def _fake_module() -> SimpleNamespace:
    async def initialize(_: str | None = None) -> None:
        return None

    async def close() -> None:
        return None

    def emit(plan_id: str, phase: str, detail: str = "") -> None:
        dialog = module._dialog_runs.get(plan_id)
        if dialog is not None:
            dialog.events.append(
                {
                    "phase": phase,
                    "detail": detail,
                },
            )

    module = SimpleNamespace(
        DialogRunState=_Dialog,
        _dialog_runs=PersistingDialogStore(),
        _dialog_runtime_context={},
        _dialog_emit=emit,
        _ledger_repository=None,
        _log=MagicMock(),
        initialize_research_ledger=initialize,
        close_research_ledger=close,
    )
    return module


def test_serializable_runtime_context_drops_objects_and_secrets(
    tmp_path: Path,
) -> None:
    context = {
        "base_branch": "develop",
        "source_root": tmp_path,
        "planning_root": tmp_path / "planning",
        "workspace": object(),
        "app_services": object(),
        "github_token": "secret",
    }

    assert serializable_runtime_context(context) == {
        "base_branch": "develop",
        "source_root": str(tmp_path),
        "planning_root": str(tmp_path / "planning"),
    }


def test_dialog_snapshot_payload_includes_restorable_runtime_fields() -> None:
    dialog = _Dialog(
        plan_id="plan-payload",
        status="approved",
    )

    payload = dialog_snapshot_payload(
        dialog,
        {
            "base_branch": "master",
            "source_root": "/tmp/repository",
            "workspace": object(),
        },
    )

    assert payload["plan_id"] == "plan-payload"
    assert payload["owner_user_id"] == "kai"
    assert payload["_runtime_context"] == {
        "base_branch": "master",
        "source_root": "/tmp/repository",
    }


@pytest.mark.parametrize(
    "status",
    ["accepted", "planning", "discovering", "planned"],
)
def test_interrupted_planning_becomes_explicit_failure(
    status: str,
) -> None:
    payload = dialog_snapshot_payload(
        _Dialog(plan_id="plan-planning", status=status),
    )

    restored, _ = recover_interrupted_dialog_payload(payload)

    assert restored["status"] == "failed"
    assert "server restart" in restored["error"]


def test_approved_snapshot_requires_explicit_reapproval() -> None:
    payload = dialog_snapshot_payload(
        _Dialog(plan_id="plan-approved", status="approved"),
    )

    restored, _ = recover_interrupted_dialog_payload(payload)

    assert restored["status"] == "awaiting_approval"
    assert "Approve it again" in restored["error"]


def test_executing_snapshot_with_worktree_becomes_recoverable() -> None:
    payload = dialog_snapshot_payload(
        _Dialog(
            plan_id="plan-executing",
            status="executing",
            worktree_path="/tmp/worktree",
        ),
        {
            "base_branch": "develop",
            "source_root": "/tmp/repository",
        },
    )

    restored, runtime = recover_interrupted_dialog_payload(payload)

    assert restored["status"] == "needs_revision"
    assert restored["validation_failure_category"] == (
        "runtime_interrupted"
    )
    assert runtime == {
        "base_branch": "develop",
        "source_root": "/tmp/repository",
    }


def test_executing_snapshot_without_worktree_returns_to_approval() -> None:
    payload = dialog_snapshot_payload(
        _Dialog(
            plan_id="plan-before-worktree",
            status="executing",
        ),
    )

    restored, _ = recover_interrupted_dialog_payload(payload)

    assert restored["status"] == "awaiting_approval"
    assert "before a worktree was prepared" in restored["error"]


def test_terminal_snapshot_is_restored_without_status_rewrite() -> None:
    payload = dialog_snapshot_payload(
        _Dialog(plan_id="plan-complete", status="completed"),
    )

    restored, _ = recover_interrupted_dialog_payload(payload)

    assert restored["status"] == "completed"
    assert restored["error"] == ""


def test_persisting_store_keeps_transition_guard() -> None:
    store = PersistingDialogStore()
    running = _Dialog(plan_id="plan-transition", status="executing")
    store[running.plan_id] = running
    completed = _Dialog(plan_id=running.plan_id, status="completed")
    store[running.plan_id] = completed

    with pytest.raises(InvalidResearchTransition):
        store[running.plan_id] = _Dialog(
            plan_id=running.plan_id,
            status="executing",
        )


@pytest.mark.asyncio
async def test_installed_store_and_emit_queue_latest_snapshot() -> None:
    module = _fake_module()
    install_research_dialog_persistence(module)
    store = SimpleNamespace(upsert=AsyncMock())
    module._dialog_snapshot_store = store
    dialog = _Dialog(
        plan_id="plan-queue",
        status="awaiting_approval",
    )

    module._dialog_runs[dialog.plan_id] = dialog
    module._dialog_emit(
        dialog.plan_id,
        "awaiting_approval",
        "Plan ready",
    )
    await module._flush_dialog_snapshots()

    assert store.upsert.await_count >= 1
    latest_payload = store.upsert.await_args_list[-1].args[0]
    assert latest_payload["plan_id"] == dialog.plan_id
    assert latest_payload["events"][-1]["phase"] == (
        "awaiting_approval"
    )


@pytest.mark.asyncio
async def test_restore_snapshots_recovers_dialog_and_runtime_context() -> None:
    module = _fake_module()
    install_research_dialog_persistence(module)
    payload = dialog_snapshot_payload(
        _Dialog(
            plan_id="plan-restore",
            status="executing",
            worktree_path="/tmp/worktree",
        ),
        {
            "base_branch": "develop",
            "source_root": "/tmp/repository",
        },
    )
    store = SimpleNamespace(
        list_recent=AsyncMock(return_value=[_snapshot(payload)]),
    )
    module._dialog_snapshot_store = store

    await module._restore_dialog_snapshots()

    restored = module._dialog_runs["plan-restore"]
    assert restored.status == "needs_revision"
    assert restored.validation_failure_category == (
        "runtime_interrupted"
    )
    assert module._dialog_runtime_context["plan-restore"] == {
        "base_branch": "develop",
        "source_root": "/tmp/repository",
    }


@pytest.mark.asyncio
async def test_restore_skips_corrupt_snapshot_and_logs_error() -> None:
    module = _fake_module()
    install_research_dialog_persistence(module)
    payload = dialog_snapshot_payload(
        _Dialog(plan_id="plan-corrupt", status="completed"),
    )
    snapshot = _snapshot(payload)
    snapshot.state_hash = "0" * 64
    module._dialog_snapshot_store = SimpleNamespace(
        list_recent=AsyncMock(return_value=[snapshot]),
    )

    await module._restore_dialog_snapshots()

    assert "plan-corrupt" not in module._dialog_runs
    module._log.exception.assert_called()


def test_real_router_has_dialog_persistence_installed() -> None:
    from qwenpaw.app.routers import research as research_module

    assert research_module._dialog_persistence_installed is True
    assert isinstance(
        research_module._dialog_runs,
        PersistingDialogStore,
    )
    assert research_module._dialog_snapshot_payload is (
        dialog_snapshot_payload
    )
    assert research_module._recover_interrupted_dialog_payload is (
        recover_interrupted_dialog_payload
    )
