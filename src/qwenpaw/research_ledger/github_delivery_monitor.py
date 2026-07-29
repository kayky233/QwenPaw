"""Read GitHub pull-request CI and review state through an injected CLI runner."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable

from .delivery_lifecycle import (
    CICheck,
    CICheckStatus,
    CIReport,
    ReviewThread,
)

RunProcess = Callable[..., Awaitable[str]]

_SUCCESS_CONCLUSIONS = {"SUCCESS", "NEUTRAL", "SKIPPED"}
_FAILED_CONCLUSIONS = {
    "FAILURE",
    "TIMED_OUT",
    "ACTION_REQUIRED",
    "STARTUP_FAILURE",
    "STALE",
}


@dataclass(frozen=True)
class GitHubDeliverySnapshot:
    ci_report: CIReport
    review_threads: tuple[ReviewThread, ...]
    review_decision: str


def _check_status(item: dict) -> CICheckStatus:
    conclusion = str(item.get("conclusion") or "").upper()
    status = str(item.get("status") or "").upper()
    if conclusion in _SUCCESS_CONCLUSIONS:
        return CICheckStatus.PASSED
    if conclusion in _FAILED_CONCLUSIONS:
        return CICheckStatus.FAILED
    if conclusion == "CANCELLED":
        return CICheckStatus.CANCELLED
    if status == "COMPLETED" and not conclusion:
        return CICheckStatus.FAILED
    return CICheckStatus.PENDING


def parse_github_delivery_snapshot(
    raw: str,
    *,
    attempt: int = 1,
) -> GitHubDeliverySnapshot:
    payload = json.loads(raw)
    checks = tuple(
        CICheck(
            name=str(item.get("name") or item.get("context") or "unknown"),
            status=_check_status(item),
            url=str(item.get("detailsUrl") or item.get("targetUrl") or ""),
            summary=str(item.get("workflowName") or ""),
        )
        for item in payload.get("statusCheckRollup", ())
        if isinstance(item, dict)
    )
    commit_sha = str(payload.get("headRefOid") or "")
    review_decision = str(payload.get("reviewDecision") or "REVIEW_REQUIRED").upper()
    review_threads: tuple[ReviewThread, ...] = ()
    if review_decision in {"CHANGES_REQUESTED", "REVIEW_REQUIRED"}:
        review_threads = (
            ReviewThread(
                thread_id="github-review-decision",
                author="github",
                body=review_decision.lower().replace("_", " "),
                resolved=False,
            ),
        )
    return GitHubDeliverySnapshot(
        ci_report=CIReport(
            commit_sha=commit_sha,
            checks=checks,
            attempt=attempt,
        ),
        review_threads=review_threads,
        review_decision=review_decision,
    )


async def fetch_github_delivery_snapshot(
    *,
    repository: str,
    pr_url: str,
    worktree: Path,
    run_process: RunProcess,
    attempt: int = 1,
) -> GitHubDeliverySnapshot:
    raw = await run_process(
        [
            "gh",
            "pr",
            "view",
            pr_url,
            "--repo",
            repository,
            "--json",
            "headRefOid,statusCheckRollup,reviewDecision",
        ],
        cwd=worktree,
        timeout=120,
    )
    return parse_github_delivery_snapshot(raw, attempt=attempt)
