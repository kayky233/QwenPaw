"""Persistence hook for dialog Step/Artifact projections."""

from __future__ import annotations

from . import research_dialog_persistence


def enable_execution_ledger_snapshot_persistence() -> None:
    """Allow the JSON-only execution ledger through the snapshot safe list."""

    research_dialog_persistence._PERSISTED_RUNTIME_KEYS = frozenset(
        {
            *research_dialog_persistence._PERSISTED_RUNTIME_KEYS,
            "execution_ledger",
        }
    )
