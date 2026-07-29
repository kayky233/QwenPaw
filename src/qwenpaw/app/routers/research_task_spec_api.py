"""Dialog and delivery projections for the resolved AutoResearch TaskSpec."""

from __future__ import annotations

from types import ModuleType
from typing import Any

from .research_task_spec import resolve_task_spec


def install_research_task_spec_api(research_module: ModuleType) -> None:
    """Expose a non-secret TaskSpec in dialog and pull-request evidence."""

    def resolved_spec(dialog: Any):
        plan_id = getattr(dialog, "plan_id", "")
        if not isinstance(plan_id, str) or not plan_id.strip():
            return None
        runtime_context = research_module._dialog_runtime_context.setdefault(
            plan_id,
            {},
        )
        return resolve_task_spec(dialog, runtime_context)

    base_dialog_payload = research_module._dialog_payload

    def dialog_payload(dialog: Any) -> dict[str, Any]:
        payload = base_dialog_payload(dialog)
        spec = resolved_spec(dialog)
        if spec is not None:
            payload["task_spec"] = spec.to_dict()
        return payload

    base_pr_body = research_module._build_dialog_pr_body

    def build_dialog_pr_body(dialog: Any) -> str:
        body = base_pr_body(dialog)
        spec = resolved_spec(dialog)
        if spec is None:
            return body
        contract = (
            f"- Task Type: `{spec.task_type.value}`\n"
            f"- Delivery Mode: `{spec.delivery.mode.value}`\n"
            f"- Validation Baseline: "
            f"`{spec.validation.baseline_expectation.value}`\n"
        )
        heading = "## AutoResearch Result\n"
        if heading in body:
            return body.replace(
                heading,
                f"{heading}\n{contract}",
                1,
            )
        return f"{contract}\n{body}"

    research_module._dialog_payload = dialog_payload
    research_module._build_dialog_pr_body = build_dialog_pr_body
    research_module._task_spec_api_installed = True
