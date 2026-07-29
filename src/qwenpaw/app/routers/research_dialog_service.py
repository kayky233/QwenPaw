"""Dialog-state helpers extracted from the monolithic research router."""

from __future__ import annotations

import hashlib
from dataclasses import asdict, replace
from datetime import datetime, timezone
from types import ModuleType
from typing import Any


def utc_now() -> str:
    """Return an ISO-8601 UTC timestamp for dialog evidence."""

    return datetime.now(timezone.utc).isoformat()


def plan_content_hash(plan_markdown: str) -> str:
    """Bind an approval to the exact UTF-8 contents of a plan revision."""

    return hashlib.sha256(plan_markdown.encode("utf-8")).hexdigest()


def dialog_payload(dialog: Any) -> dict[str, Any]:
    """Serialize a dialog state without exposing server-side ownership fields."""

    payload = asdict(dialog)
    for key in ("owner_agent_id", "owner_user_id", "owner_session_id"):
        payload.pop(key, None)
    return payload


def recover_legacy_scope_failure(
    dialog: Any,
    *,
    timestamp: str | None = None,
) -> Any:
    """Upgrade a pre-recovery scope failure while preserving its worktree."""

    marker = "paths not listed in the approved plan:"
    if (
        dialog.status != "failed"
        or marker not in dialog.error
        or not dialog.worktree_path
    ):
        return dialog

    paths = [
        path.strip()
        for path in dialog.error.partition(marker)[2].split(",")
        if path.strip()
    ]
    recovered_at = timestamp or utc_now()
    report = f"""# AutoResearch Validation Report

## Status

Recovered from a legacy scope-validation failure. No commit, push, or pull
request was created.

## Paths Outside Approved Plan

{chr(10).join(f"- `{path}`" for path in paths) or "- unavailable"}

## Validation Result

{dialog.error}

The existing worktree was preserved. Revise and reapprove the plan to continue.
"""
    preserved_report = dialog.validation_report or report
    return replace(
        dialog,
        status="needs_revision",
        unapproved_paths=paths,
        validation_failure_category="scope_mismatch",
        validation_report=preserved_report,
        verification_status="blocked",
        verification_summary="Plan revision required.",
        validation_attempts=[
            *dialog.validation_attempts,
            {
                "attempt": len(dialog.validation_attempts) + 1,
                "revision": dialog.revision,
                "timestamp": recovered_at,
                "failure_category": "scope_mismatch",
                "changed_paths": list(dialog.changed_paths),
                "unapproved_paths": paths,
                "reproduction_status": dialog.reproduction_status,
                "reproduction_summary": dialog.reproduction_summary,
                "verification_status": "blocked",
                "verification_summary": "Plan revision required.",
                "validation_report": preserved_report,
            },
        ],
        updated_at=recovered_at,
    )


def install_research_dialog_service(research_module: ModuleType) -> None:
    """Install extracted helpers while preserving the legacy router surface."""

    research_module._plan_content_hash = plan_content_hash
    research_module._dialog_payload = dialog_payload
    research_module._recover_legacy_scope_failure = recover_legacy_scope_failure
