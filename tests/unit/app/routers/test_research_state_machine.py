from dataclasses import dataclass, replace
from types import SimpleNamespace

import pytest

from qwenpaw.app.routers.research_state_machine import (
    InvalidResearchTransition,
    ResearchPhase,
    ResearchStatus,
    TransitionGuardedDialogStore,
    allowed_dialog_transitions,
    install_research_state_machine,
    is_recoverable_research_status,
    is_terminal_research_status,
    validate_dialog_transition,
)


def test_terminal_and_recoverable_statuses_are_explicit() -> None:
    assert is_terminal_research_status(ResearchStatus.COMPLETED)
    assert is_terminal_research_status("failed")
    assert is_terminal_research_status("cancelled")
    assert is_terminal_research_status("rejected")
    assert not is_terminal_research_status("needs_revision")
    assert is_recoverable_research_status(ResearchStatus.NEEDS_REVISION)
    assert not is_recoverable_research_status(ResearchStatus.FAILED)


def test_phase_names_are_stable_string_values() -> None:
    assert ResearchPhase.PREPARING_WORKTREE.value == "preparing_worktree"
    assert ResearchPhase.VALIDATION_NEEDS_REVISION.value == (
        "validation_needs_revision"
    )
    assert ResearchPhase.PR_CREATED.value == "pr_created"


def test_expected_supervised_dialog_path_is_legal() -> None:
    path = [
        "accepted",
        "planning",
        "discovering",
        "awaiting_approval",
        "approved",
        "executing",
        "needs_revision",
        "awaiting_approval",
        "approved",
        "executing",
        "completed",
    ]

    for current, target in zip(path, path[1:]):
        validate_dialog_transition(current, target)


def test_same_status_update_is_always_legal() -> None:
    validate_dialog_transition("executing", "executing")
    validate_dialog_transition(
        ResearchStatus.AWAITING_APPROVAL,
        ResearchStatus.AWAITING_APPROVAL,
    )


def test_terminal_status_cannot_resume_execution() -> None:
    with pytest.raises(
        InvalidResearchTransition,
        match="completed.*executing",
    ):
        validate_dialog_transition("completed", "executing")


def test_failed_status_only_allows_legacy_revision_recovery() -> None:
    assert allowed_dialog_transitions("failed") == frozenset({"needs_revision"})
    validate_dialog_transition("failed", "needs_revision")

    with pytest.raises(InvalidResearchTransition):
        validate_dialog_transition("failed", "executing")


def test_unknown_status_is_rejected() -> None:
    with pytest.raises(InvalidResearchTransition, match="Unknown"):
        allowed_dialog_transitions("mystery")


@dataclass
class _Dialog:
    plan_id: str
    status: str
    goal: str = "test"


def _fake_research_module() -> SimpleNamespace:
    return SimpleNamespace(
        DialogRunState=_Dialog,
        _dialog_runs={},
    )


def test_installed_guard_rejects_in_place_illegal_transition() -> None:
    research_module = _fake_research_module()
    install_research_state_machine(research_module)
    dialog = _Dialog(plan_id="plan-1", status="executing")

    dialog.status = "completed"
    with pytest.raises(InvalidResearchTransition):
        dialog.status = "executing"

    assert dialog.status == "completed"


def test_installed_store_rejects_replacement_illegal_transition() -> None:
    research_module = _fake_research_module()
    install_research_state_machine(research_module)
    original = _Dialog(plan_id="plan-1", status="executing")
    research_module._dialog_runs[original.plan_id] = original

    completed = replace(original, status="completed")
    research_module._dialog_runs[original.plan_id] = completed

    with pytest.raises(InvalidResearchTransition):
        research_module._dialog_runs[original.plan_id] = replace(
            completed,
            status="executing",
        )

    assert research_module._dialog_runs[original.plan_id].status == "completed"


def test_installer_is_idempotent_and_preserves_existing_dialogs() -> None:
    existing = _Dialog(plan_id="plan-existing", status="awaiting_approval")
    research_module = _fake_research_module()
    research_module._dialog_runs[existing.plan_id] = existing

    install_research_state_machine(research_module)
    first_store = research_module._dialog_runs
    install_research_state_machine(research_module)

    assert isinstance(first_store, TransitionGuardedDialogStore)
    assert research_module._dialog_runs is first_store
    assert research_module._dialog_runs[existing.plan_id] is existing


def test_real_router_dialog_state_has_runtime_guard_installed() -> None:
    from qwenpaw.app.routers import research as research_module

    dialog = research_module.DialogRunState(
        plan_id="plan-runtime-guard",
        status="accepted",
        goal="test lifecycle guard",
        events=[],
    )
    dialog.status = "planning"
    dialog.status = "discovering"
    dialog.status = "awaiting_approval"
    dialog.status = "approved"
    dialog.status = "executing"
    dialog.status = "completed"

    with pytest.raises(research_module.InvalidResearchTransition):
        dialog.status = "executing"
