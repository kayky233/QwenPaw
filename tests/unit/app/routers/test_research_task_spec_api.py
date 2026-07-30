from dataclasses import dataclass
from types import SimpleNamespace

from qwenpaw.app.routers.research_task_spec_api import (
    install_research_task_spec_api,
)


@dataclass
class _Dialog:
    plan_id: str = "plan-task-spec-api"
    goal: str = "Add memory TTL support"
    plan_markdown: str = "# Plan\n\nTask Type: feature\n"
    owner_agent_id: str = "default"
    owner_user_id: str = "kai"
    owner_session_id: str = "chat-1"


def _module() -> SimpleNamespace:
    return SimpleNamespace(
        _dialog_runtime_context={},
        _dialog_payload=lambda dialog: {
            "plan_id": dialog.plan_id,
            "goal": dialog.goal,
        },
        _build_dialog_pr_body=lambda dialog: (
            "## AutoResearch Result\n\n"
            f"- Goal: {dialog.goal}\n"
        ),
    )


def test_task_spec_api_adds_resolved_contract_without_owner_fields() -> None:
    module = _module()
    install_research_task_spec_api(module)

    payload = module._dialog_payload(_Dialog())

    assert payload["task_spec"]["task_type"] == "feature"
    assert payload["task_spec"]["delivery"]["commit_prefix"] == "feat"
    assert payload["task_spec"]["validation"][
        "baseline_expectation"
    ] == "informational"
    assert "owner_agent_id" not in payload
    assert "task_spec" in module._dialog_runtime_context[
        "plan-task-spec-api"
    ]


def test_task_spec_api_adds_contract_to_pr_body() -> None:
    module = _module()
    install_research_task_spec_api(module)

    body = module._build_dialog_pr_body(_Dialog())

    assert "- Task Type: `feature`" in body
    assert "- Delivery Mode: `pull_request`" in body
    assert "- Validation Baseline: `informational`" in body
    assert "- Goal: Add memory TTL support" in body


def test_task_spec_api_leaves_non_dialog_contract_unchanged() -> None:
    module = _module()
    install_research_task_spec_api(module)

    payload = module._dialog_payload(
        SimpleNamespace(goal="Legacy helper", plan_id=""),
    )

    assert payload["goal"] == "Legacy helper"
    assert "task_spec" not in payload
    assert module._dialog_runtime_context == {}


def test_real_router_exposes_task_spec_in_dialog_payload() -> None:
    from qwenpaw.app.routers import research as research_module

    plan_id = "plan-real-task-spec-api"
    dialog = research_module.DialogRunState(
        plan_id=plan_id,
        status="awaiting_approval",
        goal="Refactor the repository service",
        events=[],
        plan_markdown="# Plan\n\nTask Type: refactor\n",
        validation_report="# Validation\n\nPassed.",
        branch="autoresearch/refactor-task-planreal",
        commit_sha="a" * 40,
    )
    try:
        payload = research_module._dialog_payload(dialog)
        body = research_module._build_dialog_pr_body(dialog)

        assert payload["task_spec"]["task_type"] == "refactor"
        assert payload["task_spec"]["validation"][
            "requires_behavior_invariants"
        ] is True
        assert payload["task_spec"]["delivery"]["commit_prefix"] == (
            "refactor"
        )
        assert "owner_agent_id" not in payload
        assert "- Task Type: `refactor`" in body
        assert "- Validation Baseline: `must_pass`" in body
        assert research_module._task_spec_api_installed is True
    finally:
        research_module._dialog_runtime_context.pop(plan_id, None)
