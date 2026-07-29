from dataclasses import dataclass
from types import SimpleNamespace

from qwenpaw.app.routers.research_delivery_lifecycle_service import (
    install_research_delivery_lifecycle_service,
)
from qwenpaw.research_ledger.delivery_lifecycle import (
    CICheck,
    CICheckStatus,
    CIReport,
    ReviewThread,
)


@dataclass(frozen=True)
class Dialog:
    plan_id: str
    status: str
    auto_pr: bool
    pr_url: str
    updated_at: str = "now"


def test_pr_completion_waits_for_ci_and_review():
    dialog = Dialog(
        plan_id="plan",
        status="completed",
        auto_pr=True,
        pr_url="https://github.com/o/r/pull/1",
    )
    events = []
    module = SimpleNamespace(
        _delivery_lifecycle_installed=False,
        _dialog_runs={"plan": dialog},
        _dialog_runtime_context={},
        _dialog_emit=lambda plan_id, phase, detail="": events.append(phase),
        _dialog_payload=lambda current: {"status": current.status},
        _queue_dialog_snapshot=lambda current: None,
        _utc_now=lambda: "later",
    )

    install_research_delivery_lifecycle_service(module)
    module._dialog_emit("plan", "completed", "done")
    assert module._dialog_runs["plan"].status == "ci_waiting"
    assert events[-1] == "ci_waiting"

    module._record_dialog_ci_report(
        "plan",
        CIReport(
            commit_sha="abc",
            checks=(CICheck("tests", CICheckStatus.PASSED),),
        ),
    )
    assert module._dialog_runs["plan"].status == "review_waiting"

    module._record_dialog_review_threads(
        "plan",
        (ReviewThread("thread", "reviewer", "ok", resolved=True),),
    )
    assert module._dialog_runs["plan"].status == "merge_ready"
    payload = module._dialog_payload(module._dialog_runs["plan"])
    assert payload["delivery_lifecycle"]["status"] == "merge_ready"
