import logging
from types import SimpleNamespace

import pytest

from qwenpaw.app.routers.research_step_runtime_service import (
    DialogExecutionLedger,
    install_research_step_runtime_service,
)
from qwenpaw.research_ledger.contracts import (
    ResearchArtifactType,
    ResearchStepStatus,
    ResearchStepType,
)


def _dialog(auto_pr=True):
    return SimpleNamespace(
        plan_id="plan-1",
        auto_pr=auto_pr,
        plan_markdown="# Plan\n",
        revision=1,
        content_hash="a" * 64,
        changed_paths=[],
        commit_sha="",
        validation_report="",
        verification_status="pending",
        reproduction_status="pending",
        validation_failure_category="",
        pr_url="",
    )


def test_dialog_execution_ledger_builds_verified_evidence_chain():
    dialog = _dialog()
    ledger = DialogExecutionLedger(dialog.plan_id)

    ledger.observe("accepted", "accepted", dialog)
    ledger.observe("plan_ready", "ready", dialog)
    ledger.observe("implementing", "implement", dialog)
    ledger.observe("reproducing", "test", dialog)

    dialog.changed_paths = ["src/cache.py", "tests/test_cache.py"]
    dialog.commit_sha = "abc123"
    dialog.validation_report = "validation passed"
    dialog.verification_status = "passed"
    dialog.reproduction_status = "reproduced"
    ledger.observe("report_ready", "report", dialog)
    ledger.observe("pushing", "push", dialog)
    dialog.pr_url = "https://github.com/o/r/pull/1"
    ledger.observe("pr_created", dialog.pr_url, dialog)
    ledger.observe("completed", "done", dialog)

    steps = ledger.steps.list_for_run(dialog.plan_id)
    assert [step.step_type for step in steps] == [
        ResearchStepType.DISCOVERY,
        ResearchStepType.PLAN,
        ResearchStepType.IMPLEMENT,
        ResearchStepType.TEST,
        ResearchStepType.REVIEW,
        ResearchStepType.DELIVERY,
    ]
    assert all(step.status == ResearchStepStatus.COMPLETED for step in steps)

    delivery = steps[-1]
    assert ledger.artifacts.has_verified_type(
        delivery.step_id,
        ResearchArtifactType.COMMIT,
    )
    assert ledger.artifacts.has_verified_type(
        delivery.step_id,
        ResearchArtifactType.PULL_REQUEST,
    )

    restored = DialogExecutionLedger.from_json(ledger.to_json())
    assert restored.to_payload()["plan_id"] == dialog.plan_id
    assert len(restored.to_payload()["events"]) == 8


def test_delivery_without_auto_pr_requires_only_commit():
    dialog = _dialog(auto_pr=False)
    dialog.changed_paths = ["src/cache.py"]
    dialog.commit_sha = "abc123"
    dialog.validation_report = "passed"
    dialog.verification_status = "passed"
    dialog.reproduction_status = "reproduced"
    ledger = DialogExecutionLedger(dialog.plan_id)

    ledger.observe("pushing", "push", dialog)
    ledger.observe("completed", "done", dialog)

    delivery = ledger.steps.get("plan-1-delivery")
    assert delivery is not None
    assert delivery.status == ResearchStepStatus.COMPLETED
    assert delivery.required_artifacts == (ResearchArtifactType.COMMIT,)


@pytest.mark.asyncio
async def test_installation_exposes_ledger_in_dialog_payload():
    dialog = _dialog()
    snapshots = []

    async def close():
        return None

    module = SimpleNamespace(
        _step_runtime_service_installed=False,
        _dialog_runs={dialog.plan_id: dialog},
        _dialog_runtime_context={},
        _dialog_emit=lambda plan_id, phase, detail="": None,
        _dialog_payload=lambda current: {"plan_id": current.plan_id},
        _queue_dialog_snapshot=snapshots.append,
        close_research_ledger=close,
        _log=logging.getLogger(__name__),
    )

    install_research_step_runtime_service(module)
    module._dialog_emit(dialog.plan_id, "accepted", "accepted")

    payload = module._dialog_payload(dialog)
    assert payload["execution_ledger"]["steps"][0]["step_type"] == (
        ResearchStepType.DISCOVERY
    )
    assert "execution_ledger" in module._dialog_runtime_context[dialog.plan_id]
    assert snapshots == [dialog]

    await module.close_research_ledger()
    assert module._dialog_execution_ledgers == {}
