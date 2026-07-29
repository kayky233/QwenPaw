"""Durable snapshots for supervised AutoResearch dialog state."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import DateTime, Integer, String, Text, delete, select, update
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.orm import Mapped, mapped_column

from .schema import Base

_DIALOG_SNAPSHOT_SCHEMA_VERSION = 1


class ResearchDialogSnapshot(Base):
    """Latest restorable state for one dialog-based research run."""

    __tablename__ = "research_dialog_snapshots"

    plan_id: Mapped[str] = mapped_column(
        String(64),
        primary_key=True,
    )
    schema_version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=_DIALOG_SNAPSHOT_SCHEMA_VERSION,
    )
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
    )
    revision: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )
    content_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        default="",
    )
    state_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )
    owner_agent_id: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
    )
    owner_user_id: Mapped[Optional[str]] = mapped_column(
        String(128),
        nullable=True,
    )
    owner_session_id: Mapped[Optional[str]] = mapped_column(
        String(128),
        nullable=True,
    )
    state_json: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    def __repr__(self) -> str:
        return (
            "ResearchDialogSnapshot("
            f"plan_id={self.plan_id!r}, "
            f"status={self.status!r}, "
            f"revision={self.revision!r})"
        )


def encode_dialog_state(state: Mapping[str, Any]) -> tuple[str, str]:
    """Encode a dialog state deterministically and return its SHA-256."""

    encoded = json.dumps(
        dict(state),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    return encoded, digest


def decode_dialog_state(snapshot: ResearchDialogSnapshot) -> dict[str, Any]:
    """Decode and integrity-check one stored dialog snapshot."""

    if snapshot.schema_version != _DIALOG_SNAPSHOT_SCHEMA_VERSION:
        raise RuntimeError(
            "Unsupported AutoResearch dialog snapshot schema version: "
            f"{snapshot.schema_version}",
        )
    expected_hash = hashlib.sha256(
        snapshot.state_json.encode("utf-8"),
    ).hexdigest()
    if expected_hash != snapshot.state_hash:
        raise RuntimeError(
            f"AutoResearch dialog snapshot hash mismatch: {snapshot.plan_id}",
        )
    try:
        payload = json.loads(snapshot.state_json)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"AutoResearch dialog snapshot is invalid JSON: {snapshot.plan_id}",
        ) from exc
    if not isinstance(payload, dict):
        raise RuntimeError(
            f"AutoResearch dialog snapshot is not an object: {snapshot.plan_id}",
        )
    if payload.get("plan_id") != snapshot.plan_id:
        raise RuntimeError(
            f"AutoResearch dialog snapshot plan_id mismatch: {snapshot.plan_id}",
        )
    return payload


class ResearchDialogSnapshotStore:
    """Async Core repository for latest dialog snapshots."""

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def upsert(
        self,
        state: Mapping[str, Any],
    ) -> ResearchDialogSnapshot:
        """Insert or replace the latest snapshot for one plan ID."""

        plan_id = str(state.get("plan_id") or "").strip()
        status = str(state.get("status") or "").strip()
        owner_agent_id = str(
            state.get("owner_agent_id") or "",
        ).strip()
        if not plan_id:
            raise ValueError("Dialog snapshot requires plan_id")
        if not status:
            raise ValueError("Dialog snapshot requires status")
        if not owner_agent_id:
            raise ValueError("Dialog snapshot requires owner_agent_id")

        revision = int(state.get("revision") or 0)
        content_hash = str(state.get("content_hash") or "")
        owner_user_id = state.get("owner_user_id")
        owner_session_id = state.get("owner_session_id")
        encoded, state_hash = encode_dialog_state(state)
        now = datetime.now(timezone.utc)
        values = {
            "schema_version": _DIALOG_SNAPSHOT_SCHEMA_VERSION,
            "status": status,
            "revision": revision,
            "content_hash": content_hash,
            "state_hash": state_hash,
            "owner_agent_id": owner_agent_id,
            "owner_user_id": (
                str(owner_user_id)
                if owner_user_id is not None
                else None
            ),
            "owner_session_id": (
                str(owner_session_id)
                if owner_session_id is not None
                else None
            ),
            "state_json": encoded,
            "updated_at": now,
        }

        async with self._engine.begin() as connection:
            result = await connection.execute(
                update(ResearchDialogSnapshot)
                .where(ResearchDialogSnapshot.plan_id == plan_id)
                .values(**values),
            )
            if result.rowcount == 0:
                await connection.execute(
                    ResearchDialogSnapshot.__table__.insert().values(
                        plan_id=plan_id,
                        created_at=now,
                        **values,
                    ),
                )
        snapshot = await self.get(plan_id)
        if snapshot is None:
            raise RuntimeError(
                f"Dialog snapshot was not stored: {plan_id}",
            )
        return snapshot

    async def get(
        self,
        plan_id: str,
    ) -> ResearchDialogSnapshot | None:
        """Return the latest snapshot for one plan ID."""

        async with self._engine.connect() as connection:
            result = await connection.execute(
                select(ResearchDialogSnapshot).where(
                    ResearchDialogSnapshot.plan_id == plan_id,
                ),
            )
            row = result.first()
        if row is None:
            return None
        return ResearchDialogSnapshot(**row._mapping)

    async def list_recent(
        self,
        *,
        limit: int = 500,
        owner_agent_id: str | None = None,
        owner_user_id: str | None = None,
    ) -> Sequence[ResearchDialogSnapshot]:
        """List latest snapshots, optionally restricted to an owner."""

        if limit < 1 or limit > 5000:
            raise ValueError("Dialog snapshot limit must be between 1 and 5000")
        query = select(ResearchDialogSnapshot)
        if owner_agent_id is not None:
            query = query.where(
                ResearchDialogSnapshot.owner_agent_id == owner_agent_id,
            )
        if owner_user_id is not None:
            query = query.where(
                ResearchDialogSnapshot.owner_user_id == owner_user_id,
            )
        query = query.order_by(
            ResearchDialogSnapshot.updated_at.desc(),
        ).limit(limit)
        async with self._engine.connect() as connection:
            result = await connection.execute(query)
            rows = result.all()
        return [
            ResearchDialogSnapshot(**row._mapping)
            for row in rows
        ]

    async def delete(self, plan_id: str) -> bool:
        """Delete one snapshot and report whether a row existed."""

        async with self._engine.begin() as connection:
            result = await connection.execute(
                delete(ResearchDialogSnapshot).where(
                    ResearchDialogSnapshot.plan_id == plan_id,
                ),
            )
        return bool(result.rowcount)
