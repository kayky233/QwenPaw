"""Router integration for CI, review, and merge-readiness states."""

from __future__ import annotations

import json
from dataclasses import asdict, replace
from types import ModuleType
from typing import Any

from ...research_ledger.delivery_lifecycle import (
    CICheckStatus,
    CIReport,
    DeliveryStatus,
    ReviewThread,
)
from . import research_dialog_persistence
from . import research_state_machine
from . import research_step_runtime_service

_DELIVERY_CONTEXT_KEY = "delivery_lifecycle"


def _install_delivery_transitions() -> None:
    transitions = research_state_machine._ALLOWED_DIALOG_TRANSITIONS

    def extend(status: str, *targets: str) -> None:
        transitions[status] = frozenset({*transitions.get(status, ()), *targets})

    # The current legacy executor writes ``completed`` immediately before it
    # emits the final event. The completed -> ci_waiting transition is a narrow
    # compatibility bridge until the executor itself is fully split out.
    extend("executing", "ci_waiting")
    extend("completed", "ci_waiting")
    transitions["ci_waiting"] = frozenset(
        {"review_waiting", "merge_ready", "needs_revision", "failed", "cancelled"}
    )
    transitions["review_waiting"] = frozenset(
        {"merge_ready", "needs_revision", "failed", "cancelled"}
    )
    transitions["merge_ready"] = frozenset({"completed"})
    research_state_machine._TERMINAL_STATUSES = frozenset(
        {*research_state_machine._TERMINAL_STATUSES, "merge_ready"}
    )
    research_step_runtime_service._PHASE_TO_STEP.update(
        {
            "ci_waiting": research_step_runtime_service.ResearchStepType.DELIVERY,
            "ci_passed": research_step_runtime_service.ResearchStepType.DELIVERY,
            "ci_failed": research_step_runtime_service.ResearchStepType.DELIVERY,
            "review_waiting": research_step_runtime_service.ResearchStepType.DELIVERY,
            "changes_requested": research_step_runtime_service.ResearchStepType.DELIVERY,
            "merge_ready": research_step_runtime_service.ResearchStepType.DELIVERY,
        }
    )


def _serialize_ci_report(report: CIReport | None) -> dict[str, Any] | None:
    if report is None:
        return None
    payload = asdict(report)
    payload["status"] = report.status.value
    for check in payload["checks"]:
        status = check.get("status")
        if hasattr(status, "value"):
            check["status"] = status.value
    return payload


def _delivery_payload(context: dict[str, Any] | None) -> dict[str, Any]:
    raw = (context or {}).get(_DELIVERY_CONTEXT_KEY)
    if not isinstance(raw, str) or not raw.strip():
        return {
            "status": DeliveryStatus.DRAFT_PR.value,
            "ci_report": None,
            "review_threads": [],
        }
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return {
            "status": DeliveryStatus.DRAFT_PR.value,
            "ci_report": None,
            "review_threads": [],
            "error": "invalid persisted delivery lifecycle",
        }
    return payload if isinstance(payload, dict) else {}


def install_research_delivery_lifecycle_service(
    research_module: ModuleType,
) -> None:
    """Stop treating PR creation as completion and expose readiness updates."""

    if getattr(research_module, "_delivery_lifecycle_installed", False):
        return

    _install_delivery_transitions()
    research_dialog_persistence._PERSISTED_RUNTIME_KEYS = frozenset(
        {
            *research_dialog_persistence._PERSISTED_RUNTIME_KEYS,
            _DELIVERY_CONTEXT_KEY,
        }
    )

    def store_payload(plan_id: str, payload: dict[str, Any]) -> None:
        context = research_module._dialog_runtime_context.setdefault(plan_id, {})
        context[_DELIVERY_CONTEXT_KEY] = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
        )
        dialog = research_module._dialog_runs.get(plan_id)
        queue_snapshot = getattr(research_module, "_queue_dialog_snapshot", None)
        if dialog is not None and callable(queue_snapshot):
            queue_snapshot(dialog)

    def transition(plan_id: str, status: str, phase: str, detail: str) -> Any:
        dialog = research_module._dialog_runs.get(plan_id)
        if dialog is None:
            raise KeyError(plan_id)
        updated = replace(
            dialog,
            status=status,
            updated_at=research_module._utc_now(),
        )
        research_module._dialog_runs[plan_id] = updated
        research_module._dialog_emit(plan_id, phase, detail)
        return updated

    base_emit = research_module._dialog_emit

    def emit(plan_id: str, phase: str, detail: str = "") -> None:
        dialog = research_module._dialog_runs.get(plan_id)
        if (
            phase == "completed"
            and dialog is not None
            and getattr(dialog, "auto_pr", False)
            and str(getattr(dialog, "pr_url", "") or "").strip()
        ):
            waiting = replace(
                dialog,
                status="ci_waiting",
                updated_at=research_module._utc_now(),
            )
            research_module._dialog_runs[plan_id] = waiting
            store_payload(
                plan_id,
                {
                    "status": DeliveryStatus.CI_WAITING.value,
                    "ci_report": None,
                    "review_threads": [],
                },
            )
            base_emit(
                plan_id,
                "ci_waiting",
                "Pull Request 已创建，正在等待 CI 与代码审查。",
            )
            return
        base_emit(plan_id, phase, detail)

    def record_ci_report(plan_id: str, report: CIReport) -> dict[str, Any]:
        current = _delivery_payload(
            research_module._dialog_runtime_context.get(plan_id)
        )
        current["ci_report"] = _serialize_ci_report(report)
        if report.status == CICheckStatus.PASSED:
            current["status"] = DeliveryStatus.REVIEW_WAITING.value
            store_payload(plan_id, current)
            transition(
                plan_id,
                "review_waiting",
                "ci_passed",
                "CI 已通过，正在等待代码审查结论。",
            )
        elif report.status in {CICheckStatus.FAILED, CICheckStatus.CANCELLED}:
            current["status"] = DeliveryStatus.CI_FAILED.value
            store_payload(plan_id, current)
            transition(
                plan_id,
                "needs_revision",
                "ci_failed",
                "CI 未通过，任务已返回可修订状态。",
            )
        else:
            current["status"] = DeliveryStatus.CI_WAITING.value
            store_payload(plan_id, current)
            research_module._dialog_emit(
                plan_id,
                "ci_waiting",
                "CI 仍在运行。",
            )
        return current

    def record_review_threads(
        plan_id: str,
        threads: tuple[ReviewThread, ...],
    ) -> dict[str, Any]:
        current = _delivery_payload(
            research_module._dialog_runtime_context.get(plan_id)
        )
        current["review_threads"] = [asdict(item) for item in threads]
        unresolved = tuple(item for item in threads if not item.resolved)
        ci_status = ((current.get("ci_report") or {}).get("status"))
        if unresolved:
            current["status"] = DeliveryStatus.CHANGES_REQUESTED.value
            store_payload(plan_id, current)
            transition(
                plan_id,
                "needs_revision",
                "changes_requested",
                f"存在 {len(unresolved)} 个未解决审查线程。",
            )
        elif ci_status == CICheckStatus.PASSED.value:
            current["status"] = DeliveryStatus.MERGE_READY.value
            store_payload(plan_id, current)
            transition(
                plan_id,
                "merge_ready",
                "merge_ready",
                "本地验证、CI 与代码审查均已通过，可以人工合并。",
            )
        else:
            current["status"] = DeliveryStatus.CI_WAITING.value
            store_payload(plan_id, current)
        return current

    base_payload = research_module._dialog_payload

    def payload(dialog: Any) -> dict[str, Any]:
        result = base_payload(dialog)
        result["delivery_lifecycle"] = _delivery_payload(
            research_module._dialog_runtime_context.get(dialog.plan_id)
        )
        return result

    research_module._record_dialog_ci_report = record_ci_report
    research_module._record_dialog_review_threads = record_review_threads
    research_module._dialog_delivery_payload = _delivery_payload
    research_module._dialog_emit = emit
    research_module._dialog_payload = payload
    research_module._delivery_lifecycle_installed = True
