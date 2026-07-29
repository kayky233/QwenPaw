"""Dialog API projection for the resolved AutoResearch TaskSpec."""

from __future__ import annotations

from types import ModuleType
from typing import Any

from .research_task_spec import resolve_task_spec


def install_research_task_spec_api(research_module: ModuleType) -> None:
    """Add a non-secret TaskSpec projection to existing dialog responses."""

    base_dialog_payload = research_module._dialog_payload

    def dialog_payload(dialog: Any) -> dict[str, Any]:
        payload = base_dialog_payload(dialog)
        plan_id = getattr(dialog, "plan_id", "")
        if not isinstance(plan_id, str) or not plan_id.strip():
            return payload
        runtime_context = research_module._dialog_runtime_context.setdefault(
            plan_id,
            {},
        )
        payload["task_spec"] = resolve_task_spec(
            dialog,
            runtime_context,
        ).to_dict()
        return payload

    research_module._dialog_payload = dialog_payload
    research_module._task_spec_api_installed = True
