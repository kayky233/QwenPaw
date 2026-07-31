"""CI and review monitoring for direct Campaign Draft PR delivery."""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path
from types import ModuleType
from typing import Any

from ...research_ledger.delivery_lifecycle import CICheckStatus
from ...research_ledger.github_delivery_monitor import (
    fetch_github_delivery_snapshot,
)
from . import research_campaign_direct_service as direct_service


def _checks_payload(checks: tuple[Any, ...]) -> list[dict[str, str]]:
    return [
        {
            "name": str(item.name),
            "status": str(item.status.value),
            "url": str(item.url),
            "summary": str(item.summary),
        }
        for item in checks
    ]


async def _monitor_delivery(
    research_module: Any,
    *,
    repository: str,
    pr_url: str,
    commit_sha: str,
    worktree: Path,
    attempts: int = 10,
    interval_seconds: float = 30.0,
) -> dict[str, Any]:
    if shutil.which("gh") is None:
        return {
            "status": "monitor_unavailable",
            "reason": "GitHub CLI is not available on the QwenPaw server",
            "automatic_merge": False,
            "attempts": [],
        }

    history: list[dict[str, Any]] = []
    final_status = "ci_waiting"
    reason = "CI checks are still pending"
    for attempt in range(1, attempts + 1):
        snapshot = await fetch_github_delivery_snapshot(
            repository=repository,
            pr_url=pr_url,
            worktree=worktree,
            run_process=research_module._run_process,
            attempt=attempt,
        )
        observed_sha = snapshot.ci_report.commit_sha
        if observed_sha and observed_sha != commit_sha:
            final_status = "needs_revision"
            reason = "Pull Request HEAD does not match the verified commit"
            history.append(
                {
                    "attempt": attempt,
                    "status": final_status,
                    "commit_sha": observed_sha,
                    "review_decision": snapshot.review_decision,
                    "checks": _checks_payload(snapshot.ci_report.checks),
                    "blockers": ["commit_sha_mismatch"],
                }
            )
            break

        ci_status = snapshot.ci_report.status
        decision = snapshot.review_decision.upper()
        blockers: list[str] = []
        if ci_status == CICheckStatus.PENDING:
            final_status = "ci_waiting"
            reason = "CI checks are still pending"
            blockers.append("ci_pending")
        elif ci_status in {CICheckStatus.FAILED, CICheckStatus.CANCELLED}:
            final_status = "needs_revision"
            reason = f"CI finished with {ci_status.value}"
            blockers.append(f"ci_{ci_status.value}")
        elif decision == "CHANGES_REQUESTED":
            final_status = "needs_revision"
            reason = "GitHub review requested changes"
            blockers.append("changes_requested")
        elif decision == "APPROVED":
            final_status = "merge_ready"
            reason = (
                "CI passed and review is approved; "
                "automatic merge remains disabled"
            )
        else:
            final_status = "review_waiting"
            reason = "CI passed and human review is still required"
            blockers.append("review_required")

        history.append(
            {
                "attempt": attempt,
                "status": final_status,
                "commit_sha": observed_sha,
                "review_decision": decision,
                "checks": _checks_payload(snapshot.ci_report.checks),
                "blockers": blockers,
            }
        )
        if final_status != "ci_waiting":
            break
        if attempt < attempts:
            await asyncio.sleep(interval_seconds)

    return {
        "status": final_status,
        "reason": reason,
        "automatic_merge": False,
        "attempts": history,
    }


def install_research_campaign_monitor_service(
    research_module: ModuleType,
) -> None:
    """Monitor Draft PR delivery and add the evidence to direct API outcomes."""

    if getattr(research_module, "_campaign_monitor_service_installed", False):
        return
    execute_campaign = research_module._execute_issue_campaign
    snapshots: dict[str, dict[str, Any]] = {}

    async def execute_with_monitoring(
        module: Any,
        campaign_id: str,
        body: Any,
        **kwargs: Any,
    ):
        result = await execute_campaign(module, campaign_id, body, **kwargs)
        mode = str(getattr(body, "delivery_mode", "draft_pr"))
        outcome = result.outcome
        if mode != "draft_pr" or outcome.status.value != "delivered":
            return result
        if outcome.delivery is None or outcome.delivery_receipt is None:
            snapshots[campaign_id] = {
                "status": "monitor_unavailable",
                "reason": "Draft PR delivery receipt is missing",
                "automatic_merge": False,
                "attempts": [],
            }
            return result
        snapshots[campaign_id] = await _monitor_delivery(
            module,
            repository=str(body.repository),
            pr_url=outcome.delivery.url,
            commit_sha=outcome.delivery_receipt.publication.commit_sha,
            worktree=Path(result.worktree),
        )
        return result

    base_outcome_payload = direct_service._outcome_payload

    def outcome_payload_with_monitor(outcome: Any) -> dict[str, Any]:
        payload = base_outcome_payload(outcome)
        run_id = str(getattr(outcome.episode, "run_id", ""))
        monitor = snapshots.get(run_id)
        if monitor is not None:
            payload["delivery_lifecycle"] = monitor
        return payload

    research_module._execute_issue_campaign = execute_with_monitoring
    research_module._campaign_delivery_snapshots = snapshots
    direct_service._outcome_payload = outcome_payload_with_monitor
    research_module._campaign_monitor_service_installed = True
