"""Explicit lifecycle rules for supervised AutoResearch dialog runs."""

from __future__ import annotations

from enum import StrEnum
from types import ModuleType
from typing import Any


class ResearchStatus(StrEnum):
    """Lifecycle statuses used by research runs and dialog research."""

    QUEUED = "queued"
    RUNNING = "running"
    ACCEPTED = "accepted"
    PLANNING = "planning"
    DISCOVERING = "discovering"
    PLANNED = "planned"
    AWAITING_APPROVAL = "awaiting_approval"
    APPROVED = "approved"
    EXECUTING = "executing"
    NEEDS_REVISION = "needs_revision"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    REJECTED = "rejected"
    AUTH_REQUIRED = "auth_required"


class ResearchPhase(StrEnum):
    """Stable phase names emitted by the current supervised workflow."""

    ACCEPTED = "accepted"
    PLANNING = "planning"
    DISCOVERING = "discovering"
    ISSUES_LOADED = "issues_loaded"
    DISCOVERY_COMPLETED = "discovery_completed"
    SEARCHING = "searching"
    RETRYING = "retrying"
    ENVIRONMENT_INCOMPATIBLE = "environment_incompatible"
    AWAITING_APPROVAL = "awaiting_approval"
    PLAN_UPDATED = "plan_updated"
    PLAN_REVISION_PROPOSED = "plan_revision_proposed"
    PLAN_REVISION_ACCEPTED = "plan_revision_accepted"
    PLAN_REVISION_REJECTED = "plan_revision_rejected"
    APPROVED = "approved"
    PREPARING_WORKTREE = "preparing_worktree"
    IMPLEMENTING = "implementing"
    REPRODUCING = "reproducing"
    REPORT_READY = "report_ready"
    VALIDATION_NEEDS_REVISION = "validation_needs_revision"
    PUSHING = "pushing"
    CREATING_PR = "creating_pr"
    PR_CREATED = "pr_created"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    REJECTED = "rejected"


class InvalidResearchTransition(RuntimeError):
    """Raised when code attempts an undeclared research status transition."""


_TERMINAL_STATUSES = frozenset(
    {
        ResearchStatus.COMPLETED.value,
        ResearchStatus.FAILED.value,
        ResearchStatus.CANCELLED.value,
        ResearchStatus.REJECTED.value,
        ResearchStatus.AUTH_REQUIRED.value,
    },
)
_RECOVERABLE_STATUSES = frozenset({ResearchStatus.NEEDS_REVISION.value})

# Same-status updates are always allowed and are handled before this table.
# ``failed -> needs_revision`` is intentionally narrow: it supports recovery of
# preserved worktrees created before recoverable validation failures existed.
_ALLOWED_DIALOG_TRANSITIONS: dict[str, frozenset[str]] = {
    ResearchStatus.ACCEPTED.value: frozenset(
        {
            ResearchStatus.PLANNING.value,
            ResearchStatus.DISCOVERING.value,
            ResearchStatus.FAILED.value,
            ResearchStatus.CANCELLED.value,
        },
    ),
    ResearchStatus.PLANNING.value: frozenset(
        {
            ResearchStatus.DISCOVERING.value,
            ResearchStatus.AWAITING_APPROVAL.value,
            ResearchStatus.FAILED.value,
            ResearchStatus.CANCELLED.value,
        },
    ),
    ResearchStatus.DISCOVERING.value: frozenset(
        {
            ResearchStatus.PLANNING.value,
            ResearchStatus.AWAITING_APPROVAL.value,
            ResearchStatus.FAILED.value,
            ResearchStatus.CANCELLED.value,
        },
    ),
    ResearchStatus.PLANNED.value: frozenset(
        {
            ResearchStatus.AWAITING_APPROVAL.value,
            ResearchStatus.FAILED.value,
            ResearchStatus.CANCELLED.value,
        },
    ),
    ResearchStatus.AWAITING_APPROVAL.value: frozenset(
        {
            ResearchStatus.APPROVED.value,
            ResearchStatus.REJECTED.value,
            ResearchStatus.FAILED.value,
            ResearchStatus.CANCELLED.value,
        },
    ),
    ResearchStatus.APPROVED.value: frozenset(
        {
            ResearchStatus.EXECUTING.value,
            ResearchStatus.FAILED.value,
            ResearchStatus.CANCELLED.value,
        },
    ),
    ResearchStatus.EXECUTING.value: frozenset(
        {
            ResearchStatus.NEEDS_REVISION.value,
            ResearchStatus.COMPLETED.value,
            ResearchStatus.FAILED.value,
            ResearchStatus.CANCELLED.value,
        },
    ),
    ResearchStatus.NEEDS_REVISION.value: frozenset(
        {
            ResearchStatus.AWAITING_APPROVAL.value,
            ResearchStatus.APPROVED.value,
            ResearchStatus.REJECTED.value,
            ResearchStatus.FAILED.value,
            ResearchStatus.CANCELLED.value,
        },
    ),
    ResearchStatus.FAILED.value: frozenset(
        {ResearchStatus.NEEDS_REVISION.value},
    ),
    ResearchStatus.COMPLETED.value: frozenset(),
    ResearchStatus.CANCELLED.value: frozenset(),
    ResearchStatus.REJECTED.value: frozenset(),
    ResearchStatus.AUTH_REQUIRED.value: frozenset(),
}


def _status_value(status: str | ResearchStatus) -> str:
    return status.value if isinstance(status, ResearchStatus) else str(status)


def is_terminal_research_status(status: str | ResearchStatus) -> bool:
    """Return whether a lifecycle status must not resume normal execution."""

    return _status_value(status) in _TERMINAL_STATUSES


def is_recoverable_research_status(status: str | ResearchStatus) -> bool:
    """Return whether a lifecycle status is explicitly waiting for recovery."""

    return _status_value(status) in _RECOVERABLE_STATUSES


def allowed_dialog_transitions(
    status: str | ResearchStatus,
) -> frozenset[str]:
    """Return declared next statuses for a supervised dialog status."""

    current = _status_value(status)
    try:
        return _ALLOWED_DIALOG_TRANSITIONS[current]
    except KeyError as exc:
        raise InvalidResearchTransition(
            f"Unknown AutoResearch dialog status: {current!r}",
        ) from exc


def validate_dialog_transition(
    current: str | ResearchStatus,
    target: str | ResearchStatus,
) -> None:
    """Reject undeclared transitions without mutating the dialog state."""

    current_value = _status_value(current)
    target_value = _status_value(target)
    if current_value == target_value:
        return
    if target_value not in allowed_dialog_transitions(current_value):
        raise InvalidResearchTransition(
            "Illegal AutoResearch dialog transition: "
            f"{current_value!r} -> {target_value!r}",
        )


class TransitionGuardedDialogStore(dict[str, Any]):
    """Dictionary that validates replacement-style dialog state updates."""

    def __setitem__(self, plan_id: str, dialog: Any) -> None:
        previous = self.get(plan_id)
        if previous is not None and previous is not dialog:
            previous_status = getattr(previous, "status", None)
            next_status = getattr(dialog, "status", None)
            if previous_status is not None and next_status is not None:
                validate_dialog_transition(previous_status, next_status)
        super().__setitem__(plan_id, dialog)


def _install_mutable_status_guard(dialog_type: type[Any]) -> None:
    if getattr(dialog_type, "__research_status_guard_installed__", False):
        return

    original_setattr = dialog_type.__setattr__

    def guarded_setattr(self: Any, name: str, value: Any) -> None:
        if name == "status" and "status" in getattr(self, "__dict__", {}):
            validate_dialog_transition(self.__dict__["status"], value)
        original_setattr(self, name, value)

    dialog_type.__setattr__ = guarded_setattr  # type: ignore[method-assign]
    dialog_type.__research_status_guard_installed__ = True


def install_research_state_machine(research_module: ModuleType) -> None:
    """Install lifecycle guards without changing the public research API."""

    dialog_type = research_module.DialogRunState
    _install_mutable_status_guard(dialog_type)

    current_store = research_module._dialog_runs
    if not isinstance(current_store, TransitionGuardedDialogStore):
        research_module._dialog_runs = TransitionGuardedDialogStore(current_store)

    # Expose small helpers on the legacy module while callers are gradually
    # migrated away from the monolithic router implementation.
    research_module.ResearchStatus = ResearchStatus
    research_module.ResearchPhase = ResearchPhase
    research_module.InvalidResearchTransition = InvalidResearchTransition
    research_module._validate_dialog_transition = validate_dialog_transition
    research_module._is_terminal_research_status = is_terminal_research_status
    research_module._is_recoverable_research_status = is_recoverable_research_status
