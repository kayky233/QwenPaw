import hashlib
from dataclasses import dataclass, field
from types import SimpleNamespace

from qwenpaw.app.routers.research_dialog_service import (
    dialog_payload,
    install_research_dialog_service,
    plan_content_hash,
    recover_legacy_scope_failure,
)


@dataclass
class _Dialog:
    plan_id: str
    status: str
    goal: str
    events: list[dict] = field(default_factory=list)
    owner_agent_id: str = "agent-private"
    owner_user_id: str | None = "user-private"
    owner_session_id: str | None = "session-private"
    error: str = ""
    worktree_path: str = ""
    validation_report: str = ""
    validation_attempts: list[dict] = field(default_factory=list)
    revision: int = 1
    changed_paths: list[str] = field(default_factory=list)
    unapproved_paths: list[str] = field(default_factory=list)
    validation_failure_category: str = ""
    reproduction_status: str = "pending"
    reproduction_summary: str = ""
    verification_status: str = "pending"
    verification_summary: str = ""
    updated_at: str = ""


def test_plan_content_hash_binds_exact_utf8_content() -> None:
    plan = "# 计划\n\n## Modifiable Files\n- src/fix.py\n"
    expected = hashlib.sha256(plan.encode("utf-8")).hexdigest()

    assert plan_content_hash(plan) == expected
    assert plan_content_hash(plan) != plan_content_hash(plan + "\n")


def test_dialog_payload_hides_server_side_owner_identity() -> None:
    dialog = _Dialog(plan_id="plan-1", status="accepted", goal="test")

    payload = dialog_payload(dialog)

    assert payload["plan_id"] == "plan-1"
    assert payload["status"] == "accepted"
    assert "owner_agent_id" not in payload
    assert "owner_user_id" not in payload
    assert "owner_session_id" not in payload


def test_legacy_scope_failure_is_upgraded_with_preserved_evidence() -> None:
    dialog = _Dialog(
        plan_id="plan-legacy",
        status="failed",
        goal="fix issue",
        error=(
            "RuntimeError: Repository changes include paths not listed in "
            "the approved plan: tests/test_fix.py, src/extra.py"
        ),
        worktree_path="/tmp/research-worktree",
        changed_paths=["src/fix.py", "tests/test_fix.py", "src/extra.py"],
        reproduction_status="pending",
    )

    recovered = recover_legacy_scope_failure(
        dialog,
        timestamp="2026-07-29T10:00:00+00:00",
    )

    assert recovered is not dialog
    assert recovered.status == "needs_revision"
    assert recovered.unapproved_paths == ["tests/test_fix.py", "src/extra.py"]
    assert recovered.validation_failure_category == "scope_mismatch"
    assert recovered.verification_status == "blocked"
    assert recovered.updated_at == "2026-07-29T10:00:00+00:00"
    assert recovered.validation_attempts[-1]["attempt"] == 1
    assert recovered.validation_attempts[-1]["changed_paths"] == (
        dialog.changed_paths
    )
    assert "tests/test_fix.py" in recovered.validation_report
    assert "existing worktree was preserved" in recovered.validation_report


def test_legacy_recovery_preserves_existing_validation_report() -> None:
    dialog = _Dialog(
        plan_id="plan-existing-report",
        status="failed",
        goal="fix issue",
        error=(
            "Repository changes include paths not listed in the approved plan: "
            "tests/test_fix.py"
        ),
        worktree_path="/tmp/research-worktree",
        validation_report="# Existing evidence",
    )

    recovered = recover_legacy_scope_failure(
        dialog,
        timestamp="2026-07-29T10:00:00+00:00",
    )

    assert recovered.validation_report == "# Existing evidence"
    assert recovered.validation_attempts[-1]["validation_report"] == (
        "# Existing evidence"
    )


def test_non_legacy_failure_is_not_modified() -> None:
    dialog = _Dialog(
        plan_id="plan-normal-failure",
        status="failed",
        goal="fix issue",
        error="candidate validation failed",
        worktree_path="/tmp/research-worktree",
    )

    assert recover_legacy_scope_failure(dialog) is dialog


def test_legacy_failure_without_worktree_is_not_recoverable() -> None:
    dialog = _Dialog(
        plan_id="plan-no-worktree",
        status="failed",
        goal="fix issue",
        error=(
            "Repository changes include paths not listed in the approved plan: "
            "tests/test_fix.py"
        ),
    )

    assert recover_legacy_scope_failure(dialog) is dialog


def test_installer_exposes_helpers_on_legacy_router_surface() -> None:
    module = SimpleNamespace()

    install_research_dialog_service(module)

    assert module._plan_content_hash is plan_content_hash
    assert module._dialog_payload is dialog_payload
    assert module._recover_legacy_scope_failure is recover_legacy_scope_failure


def test_real_router_preserves_extracted_dialog_behavior() -> None:
    from qwenpaw.app.routers import research as research_module

    plan = "# Plan\n"
    assert research_module._plan_content_hash(plan) == plan_content_hash(plan)

    dialog = research_module.DialogRunState(
        plan_id="dialog-service-behavior",
        status="accepted",
        goal="test",
        events=[],
    )
    payload = research_module._dialog_payload(dialog)
    assert payload["plan_id"] == dialog.plan_id
    assert payload["status"] == dialog.status
    assert "owner_agent_id" not in payload
