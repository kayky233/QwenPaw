from collections.abc import AsyncIterator

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from qwenpaw.research_ledger.dialog_snapshot import (
    ResearchDialogSnapshot,
    ResearchDialogSnapshotStore,
    decode_dialog_state,
    encode_dialog_state,
)
from qwenpaw.research_ledger.schema import Base


@pytest.fixture
async def snapshot_engine() -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    try:
        yield engine
    finally:
        await engine.dispose()


def _state(**overrides):
    state = {
        "plan_id": "plan-snapshot-1",
        "status": "awaiting_approval",
        "goal": "Fix issue 42",
        "events": [],
        "revision": 3,
        "content_hash": "a" * 64,
        "owner_agent_id": "default",
        "owner_user_id": "kai",
        "owner_session_id": "chat-1",
        "plan_markdown": "# Plan",
        "validation_attempts": [],
    }
    state.update(overrides)
    return state


def _copy_snapshot(
    snapshot: ResearchDialogSnapshot,
    **overrides,
) -> ResearchDialogSnapshot:
    values = {
        "plan_id": snapshot.plan_id,
        "schema_version": snapshot.schema_version,
        "status": snapshot.status,
        "revision": snapshot.revision,
        "content_hash": snapshot.content_hash,
        "state_hash": snapshot.state_hash,
        "owner_agent_id": snapshot.owner_agent_id,
        "owner_user_id": snapshot.owner_user_id,
        "owner_session_id": snapshot.owner_session_id,
        "state_json": snapshot.state_json,
        "created_at": snapshot.created_at,
        "updated_at": snapshot.updated_at,
    }
    values.update(overrides)
    return ResearchDialogSnapshot(**values)


def test_dialog_snapshot_table_is_registered_with_ledger_metadata() -> None:
    assert "research_dialog_snapshots" in Base.metadata.tables


def test_encode_dialog_state_is_deterministic_and_unicode_safe() -> None:
    first = _state(goal="修复审批恢复")
    second = {
        key: first[key]
        for key in reversed(first)
    }

    first_json, first_hash = encode_dialog_state(first)
    second_json, second_hash = encode_dialog_state(second)

    assert first_json == second_json
    assert first_hash == second_hash
    assert "修复审批恢复" in first_json


@pytest.mark.asyncio
async def test_snapshot_store_inserts_and_decodes_state(
    snapshot_engine: AsyncEngine,
) -> None:
    store = ResearchDialogSnapshotStore(snapshot_engine)
    state = _state()

    snapshot = await store.upsert(state)

    assert snapshot.plan_id == state["plan_id"]
    assert snapshot.status == "awaiting_approval"
    assert snapshot.revision == 3
    assert snapshot.owner_agent_id == "default"
    assert decode_dialog_state(snapshot) == state


@pytest.mark.asyncio
async def test_snapshot_store_updates_one_existing_row(
    snapshot_engine: AsyncEngine,
) -> None:
    store = ResearchDialogSnapshotStore(snapshot_engine)
    original = await store.upsert(_state())
    updated_state = _state(
        status="approved",
        revision=4,
        content_hash="b" * 64,
        approved_by="kai",
    )

    updated = await store.upsert(updated_state)

    assert updated.status == "approved"
    assert updated.revision == 4
    assert updated.content_hash == "b" * 64
    assert updated.created_at == original.created_at
    assert decode_dialog_state(updated) == updated_state
    async with snapshot_engine.connect() as connection:
        count = await connection.scalar(
            select(func.count()).select_from(
                ResearchDialogSnapshot,
            ),
        )
    assert count == 1


@pytest.mark.asyncio
async def test_snapshot_store_lists_recent_snapshots_by_owner(
    snapshot_engine: AsyncEngine,
) -> None:
    store = ResearchDialogSnapshotStore(snapshot_engine)
    await store.upsert(_state(plan_id="plan-a"))
    await store.upsert(
        _state(
            plan_id="plan-b",
            owner_user_id="other-user",
        ),
    )
    await store.upsert(
        _state(
            plan_id="plan-c",
            owner_agent_id="reviewer",
        ),
    )

    user_rows = await store.list_recent(owner_user_id="kai")
    agent_rows = await store.list_recent(owner_agent_id="default")

    assert {row.plan_id for row in user_rows} == {
        "plan-a",
        "plan-c",
    }
    assert {row.plan_id for row in agent_rows} == {
        "plan-a",
        "plan-b",
    }


@pytest.mark.asyncio
async def test_snapshot_store_rejects_missing_identity_fields(
    snapshot_engine: AsyncEngine,
) -> None:
    store = ResearchDialogSnapshotStore(snapshot_engine)

    with pytest.raises(ValueError, match="plan_id"):
        await store.upsert(_state(plan_id=""))
    with pytest.raises(ValueError, match="status"):
        await store.upsert(_state(status=""))
    with pytest.raises(ValueError, match="owner_agent_id"):
        await store.upsert(_state(owner_agent_id=""))


@pytest.mark.asyncio
async def test_snapshot_integrity_check_detects_tampering(
    snapshot_engine: AsyncEngine,
) -> None:
    store = ResearchDialogSnapshotStore(snapshot_engine)
    snapshot = await store.upsert(_state())
    tampered = _copy_snapshot(
        snapshot,
        state_json=snapshot.state_json.replace(
            "awaiting_approval",
            "approved",
        ),
    )

    with pytest.raises(RuntimeError, match="hash mismatch"):
        decode_dialog_state(tampered)


def test_snapshot_decode_rejects_plan_id_mismatch() -> None:
    encoded, digest = encode_dialog_state(
        _state(plan_id="different-plan"),
    )
    snapshot = ResearchDialogSnapshot(
        plan_id="plan-snapshot-1",
        schema_version=1,
        status="awaiting_approval",
        revision=1,
        content_hash="",
        state_hash=digest,
        owner_agent_id="default",
        owner_user_id=None,
        owner_session_id=None,
        state_json=encoded,
    )

    with pytest.raises(RuntimeError, match="plan_id mismatch"):
        decode_dialog_state(snapshot)


def test_snapshot_decode_rejects_unknown_schema_version() -> None:
    encoded, digest = encode_dialog_state(_state())
    snapshot = ResearchDialogSnapshot(
        plan_id="plan-snapshot-1",
        schema_version=99,
        status="awaiting_approval",
        revision=1,
        content_hash="",
        state_hash=digest,
        owner_agent_id="default",
        owner_user_id=None,
        owner_session_id=None,
        state_json=encoded,
    )

    with pytest.raises(RuntimeError, match="schema version"):
        decode_dialog_state(snapshot)


@pytest.mark.asyncio
async def test_snapshot_store_deletes_existing_state(
    snapshot_engine: AsyncEngine,
) -> None:
    store = ResearchDialogSnapshotStore(snapshot_engine)
    await store.upsert(_state())

    assert await store.delete("plan-snapshot-1") is True
    assert await store.delete("plan-snapshot-1") is False
    assert await store.get("plan-snapshot-1") is None


@pytest.mark.asyncio
async def test_snapshot_store_validates_list_limit(
    snapshot_engine: AsyncEngine,
) -> None:
    store = ResearchDialogSnapshotStore(snapshot_engine)

    with pytest.raises(ValueError, match="between 1 and 5000"):
        await store.list_recent(limit=0)
    with pytest.raises(ValueError, match="between 1 and 5000"):
        await store.list_recent(limit=5001)
