"""Research Ledger integration for supervised dialog state snapshots."""

from __future__ import annotations

import asyncio
from dataclasses import asdict
from pathlib import Path
from types import ModuleType
from typing import Any, Callable

from ...research_ledger.dialog_snapshot import (
    ResearchDialogSnapshotStore,
    decode_dialog_state,
)
from .research_state_machine import TransitionGuardedDialogStore

_PERSISTED_RUNTIME_KEYS = frozenset(
    {
        "base_branch",
        "base_branch_source",
        "planning_root",
        "source_root",
    },
)
_INTERRUPTED_PLANNING_STATUSES = frozenset(
    {
        "accepted",
        "planning",
        "discovering",
        "planned",
    },
)


def serializable_runtime_context(
    runtime_context: dict[str, Any] | None,
) -> dict[str, str]:
    """Keep only stable, non-secret runtime values in a dialog snapshot."""

    if not runtime_context:
        return {}
    persisted: dict[str, str] = {}
    for key in _PERSISTED_RUNTIME_KEYS:
        value = runtime_context.get(key)
        if isinstance(value, (str, Path)) and str(value).strip():
            persisted[key] = str(value)
    return persisted


def dialog_snapshot_payload(
    dialog: Any,
    runtime_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create the durable JSON payload for one dialog dataclass."""

    payload = asdict(dialog)
    payload["_runtime_context"] = serializable_runtime_context(
        runtime_context,
    )
    return payload


def recover_interrupted_dialog_payload(
    payload: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, str]]:
    """Convert non-resumable process states into explicit recovery states."""

    restored = dict(payload)
    runtime_context = restored.pop("_runtime_context", {})
    if not isinstance(runtime_context, dict):
        runtime_context = {}
    runtime_context = {
        str(key): str(value)
        for key, value in runtime_context.items()
        if key in _PERSISTED_RUNTIME_KEYS
        and isinstance(value, (str, Path))
        and str(value).strip()
    }

    status = str(restored.get("status") or "")
    if status in _INTERRUPTED_PLANNING_STATUSES:
        restored["status"] = "failed"
        restored["error"] = (
            "AutoResearch planning was interrupted by a server restart. "
            "Start a new research request to regenerate the plan."
        )
    elif status == "approved":
        restored["status"] = "awaiting_approval"
        restored["error"] = (
            "The approved plan was restored after a server restart. "
            "Approve it again before repository execution resumes."
        )
    elif status == "executing":
        if str(restored.get("worktree_path") or "").strip():
            restored["status"] = "needs_revision"
            restored["validation_failure_category"] = (
                "runtime_interrupted"
            )
            restored["error"] = (
                "Repository execution was interrupted by a server restart. "
                "The preserved worktree requires explicit reapproval."
            )
        else:
            restored["status"] = "awaiting_approval"
            restored["error"] = (
                "Repository execution was interrupted before a worktree was "
                "prepared. Approve the restored plan again to continue."
            )
    return restored, runtime_context


class PersistingDialogStore(TransitionGuardedDialogStore):
    """Transition-guarded store that schedules snapshots on replacement."""

    def __init__(
        self,
        initial: dict[str, Any] | None = None,
        *,
        callback: Callable[[Any], None] | None = None,
    ) -> None:
        super().__init__(initial or {})
        self._snapshot_callback = callback
        self._snapshot_suppressed = False

    def set_snapshot_callback(
        self,
        callback: Callable[[Any], None],
    ) -> None:
        self._snapshot_callback = callback

    def suppress_snapshots(self, suppressed: bool) -> None:
        self._snapshot_suppressed = suppressed

    def __setitem__(self, plan_id: str, dialog: Any) -> None:
        super().__setitem__(plan_id, dialog)
        if (
            not self._snapshot_suppressed
            and self._snapshot_callback is not None
        ):
            self._snapshot_callback(dialog)


def install_research_dialog_persistence(
    research_module: ModuleType,
) -> None:
    """Install durable dialog snapshots around existing Router behavior."""

    if getattr(
        research_module,
        "_dialog_persistence_installed",
        False,
    ):
        return

    research_module._dialog_snapshot_store = None
    research_module._dialog_snapshot_tails = {}
    research_module._dialog_snapshot_errors = {}

    def queue_snapshot(dialog: Any) -> None:
        if getattr(
            research_module,
            "_dialog_snapshot_restoring",
            False,
        ):
            return
        store = research_module._dialog_snapshot_store
        if store is None:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return

        plan_id = dialog.plan_id
        payload = dialog_snapshot_payload(
            dialog,
            research_module._dialog_runtime_context.get(plan_id),
        )
        previous = research_module._dialog_snapshot_tails.get(plan_id)

        async def persist() -> None:
            if previous is not None:
                try:
                    await previous
                except Exception:
                    pass
            try:
                await store.upsert(payload)
            except Exception as exc:
                research_module._dialog_snapshot_errors[plan_id] = exc
                research_module._log.exception(
                    "Dialog snapshot persistence failed for %s",
                    plan_id,
                )

        task = loop.create_task(persist())
        research_module._dialog_snapshot_tails[plan_id] = task

    async def flush_snapshots() -> None:
        tails = tuple(
            research_module._dialog_snapshot_tails.values(),
        )
        if tails:
            await asyncio.gather(*tails, return_exceptions=True)
        research_module._dialog_snapshot_tails.clear()

    async def restore_snapshots() -> None:
        store = research_module._dialog_snapshot_store
        if store is None:
            return
        snapshots = await store.list_recent(limit=500)
        restored_dialogs: list[Any] = []
        research_module._dialog_snapshot_restoring = True
        dialog_store = research_module._dialog_runs
        if isinstance(dialog_store, PersistingDialogStore):
            dialog_store.suppress_snapshots(True)
        try:
            for snapshot in reversed(snapshots):
                if snapshot.plan_id in dialog_store:
                    continue
                try:
                    payload = decode_dialog_state(snapshot)
                    restored, runtime_context = (
                        recover_interrupted_dialog_payload(payload)
                    )
                    dialog = research_module.DialogRunState(**restored)
                except Exception:
                    research_module._log.exception(
                        "Skipping invalid dialog snapshot %s",
                        snapshot.plan_id,
                    )
                    continue
                dialog_store[snapshot.plan_id] = dialog
                restored_dialogs.append(dialog)
                if runtime_context:
                    research_module._dialog_runtime_context[
                        snapshot.plan_id
                    ] = runtime_context
        finally:
            if isinstance(dialog_store, PersistingDialogStore):
                dialog_store.suppress_snapshots(False)
            research_module._dialog_snapshot_restoring = False

        for dialog in restored_dialogs:
            queue_snapshot(dialog)
        await flush_snapshots()

    current_store = research_module._dialog_runs
    if isinstance(current_store, PersistingDialogStore):
        current_store.set_snapshot_callback(queue_snapshot)
    else:
        research_module._dialog_runs = PersistingDialogStore(
            current_store,
            callback=queue_snapshot,
        )

    base_emit = research_module._dialog_emit

    def emit(plan_id: str, phase: str, detail: str = "") -> None:
        base_emit(plan_id, phase, detail)
        dialog = research_module._dialog_runs.get(plan_id)
        if dialog is not None:
            queue_snapshot(dialog)

    base_initialize = research_module.initialize_research_ledger

    async def initialize(
        database_url: str | None = None,
    ) -> None:
        await base_initialize(database_url)
        repository = research_module._ledger_repository
        if repository is None:
            return
        engine_getter = getattr(repository, "_get_engine", None)
        if not callable(engine_getter):
            raise RuntimeError(
                "Research Ledger repository does not expose its engine",
            )
        research_module._dialog_snapshot_store = (
            ResearchDialogSnapshotStore(engine_getter())
        )
        await restore_snapshots()

    base_close = research_module.close_research_ledger

    async def close() -> None:
        for dialog in tuple(research_module._dialog_runs.values()):
            queue_snapshot(dialog)
        await flush_snapshots()
        research_module._dialog_snapshot_store = None
        await base_close()
        research_module._dialog_runs.clear()
        research_module._dialog_snapshot_errors.clear()

    research_module._dialog_snapshot_payload = dialog_snapshot_payload
    research_module._recover_interrupted_dialog_payload = (
        recover_interrupted_dialog_payload
    )
    research_module._queue_dialog_snapshot = queue_snapshot
    research_module._flush_dialog_snapshots = flush_snapshots
    research_module._restore_dialog_snapshots = restore_snapshots
    research_module._dialog_emit = emit
    research_module.initialize_research_ledger = initialize
    research_module.close_research_ledger = close
    research_module._dialog_persistence_installed = True
