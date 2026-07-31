# -*- coding: utf-8 -*-
"""Human-controlled promotion and cleanup for guarded remote Campaign E2E PRs.

This module can mark a verified Draft PR as ready for human review, or close a
known E2E PR and delete its protected test branch. It intentionally exposes no
merge operation.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Protocol

PROMOTE_CONFIRMATION = "PROMOTE_AUTORESEARCH_DRAFT_PR"
CLEANUP_CONFIRMATION = "CLOSE_AUTORESEARCH_E2E_PR"
_BRANCH_PREFIX = "autoresearch/e2e/"
_BODY_MARKER = "AutoResearch guarded remote E2E"
_PR_URL_RE = re.compile(
    r"^https://github\.com/(?P<repository>[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)"
    r"/pull/(?P<number>\d+)(?:$|[/?#])"
)
_SUCCESS_CONCLUSIONS = {"SUCCESS", "NEUTRAL", "SKIPPED"}


@dataclass(frozen=True)
class RemotePullRequestState:
    repository: str
    url: str
    number: int
    state: str
    draft: bool
    head_branch: str
    head_commit: str
    title: str
    body: str
    review_decision: str
    checks: tuple[dict[str, str], ...]


@dataclass(frozen=True)
class RemoteCampaignControlResult:
    action: str
    status: str
    repository: str
    pull_request_url: str
    pull_request_number: int
    branch: str
    commit_sha: str
    automatic_merge: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class RemotePullRequestControlClient(Protocol):
    def inspect(
        self,
        repository: str,
        pull_request_url: str,
    ) -> RemotePullRequestState: ...

    def mark_ready(
        self,
        repository: str,
        pull_request_url: str,
    ) -> None: ...

    def close_and_delete_branch(
        self,
        repository: str,
        pull_request_url: str,
    ) -> None: ...


class GitHubPullRequestControlClient:
    """Use authenticated ``gh`` commands for non-merge PR control actions."""

    def __init__(self, *, timeout_seconds: int = 120) -> None:
        self.timeout_seconds = timeout_seconds

    def _run(self, argv: list[str]) -> str:
        if shutil.which("gh") is None:
            raise RuntimeError("remote Campaign control requires an authenticated gh CLI")
        completed = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            check=False,
            timeout=self.timeout_seconds,
        )
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout).strip()
            raise RuntimeError(f"GitHub PR control failed: {detail}")
        return completed.stdout

    def inspect(
        self,
        repository: str,
        pull_request_url: str,
    ) -> RemotePullRequestState:
        payload = json.loads(
            self._run(
                [
                    "gh",
                    "pr",
                    "view",
                    pull_request_url,
                    "--repo",
                    repository,
                    "--json",
                    (
                        "url,number,state,isDraft,headRefName,headRefOid,title,body,"
                        "reviewDecision,statusCheckRollup"
                    ),
                ]
            )
        )
        checks = tuple(
            {
                "name": str(item.get("name") or item.get("context") or "unknown"),
                "status": str(item.get("status") or ""),
                "conclusion": str(item.get("conclusion") or ""),
            }
            for item in payload.get("statusCheckRollup", ())
            if isinstance(item, dict)
        )
        return RemotePullRequestState(
            repository=repository,
            url=str(payload.get("url") or pull_request_url),
            number=int(payload.get("number") or 0),
            state=str(payload.get("state") or "UNKNOWN").upper(),
            draft=bool(payload.get("isDraft", False)),
            head_branch=str(payload.get("headRefName") or ""),
            head_commit=str(payload.get("headRefOid") or ""),
            title=str(payload.get("title") or ""),
            body=str(payload.get("body") or ""),
            review_decision=str(
                payload.get("reviewDecision") or "REVIEW_REQUIRED"
            ).upper(),
            checks=checks,
        )

    def mark_ready(
        self,
        repository: str,
        pull_request_url: str,
    ) -> None:
        self._run(
            [
                "gh",
                "pr",
                "ready",
                pull_request_url,
                "--repo",
                repository,
            ]
        )

    def close_and_delete_branch(
        self,
        repository: str,
        pull_request_url: str,
    ) -> None:
        self._run(
            [
                "gh",
                "pr",
                "close",
                pull_request_url,
                "--repo",
                repository,
                "--delete-branch",
            ]
        )


def _load_remote_report(path: Path) -> dict[str, Any]:
    path = path.expanduser().resolve()
    if not path.is_file():
        raise ValueError(f"remote Campaign report is missing: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    result = payload.get("result")
    if not isinstance(result, dict):
        raise ValueError("remote Campaign report is missing its result payload")
    return payload


def _allowed_repository(
    repository: str,
    allowed_repositories: tuple[str, ...],
) -> None:
    allowed = {item.strip().casefold() for item in allowed_repositories}
    if not allowed or repository.casefold() not in allowed:
        raise RuntimeError("Pull Request repository is not in the control allowlist")


def _validate_report_identity(
    payload: dict[str, Any],
    allowed_repositories: tuple[str, ...],
) -> tuple[dict[str, Any], str, str, int]:
    result = dict(payload["result"])
    repository = str(result.get("repository") or "")
    url = str(result.get("pull_request_url") or "")
    match = _PR_URL_RE.match(url)
    if match is None:
        raise RuntimeError("remote Campaign report has an invalid GitHub PR URL")
    if match.group("repository").casefold() != repository.casefold():
        raise RuntimeError("remote Campaign report repository and PR URL disagree")
    _allowed_repository(repository, allowed_repositories)
    number = int(match.group("number"))
    recorded_number = result.get("pull_request_number")
    if recorded_number is not None and int(recorded_number) != number:
        raise RuntimeError("remote Campaign report PR number is inconsistent")
    if bool(result.get("automatic_merge", False)):
        raise RuntimeError("remote Campaign report unexpectedly enables auto merge")
    return result, repository, url, number


def _validate_live_identity(
    live: RemotePullRequestState,
    *,
    repository: str,
    url: str,
    branch: str,
    commit_sha: str,
) -> None:
    if live.repository.casefold() != repository.casefold():
        raise RuntimeError("live Pull Request repository changed")
    if live.url.rstrip("/") != url.rstrip("/"):
        raise RuntimeError("live Pull Request URL changed")
    if live.state != "OPEN":
        raise RuntimeError("Pull Request is not open")
    if live.head_branch != branch or not branch.startswith(_BRANCH_PREFIX):
        raise RuntimeError("Pull Request branch is not a protected E2E branch")
    if live.head_commit.casefold() != commit_sha.casefold():
        raise RuntimeError("Pull Request HEAD no longer matches verified commit")
    if _BODY_MARKER not in live.body:
        raise RuntimeError("Pull Request is missing the AutoResearch E2E marker")


def _checks_passed(checks: tuple[dict[str, str], ...]) -> bool:
    if not checks:
        return False
    for item in checks:
        conclusion = str(item.get("conclusion") or "").upper()
        if conclusion not in _SUCCESS_CONCLUSIONS:
            return False
    return True


def promote_remote_e2e_draft(
    report_path: Path,
    *,
    confirmation: str,
    allowed_repositories: tuple[str, ...],
    client: RemotePullRequestControlClient | None = None,
) -> RemoteCampaignControlResult:
    """Mark a fully validated E2E Draft PR ready; never merge it."""

    if confirmation != PROMOTE_CONFIRMATION:
        raise RuntimeError("Draft PR promotion confirmation mismatch")
    payload = _load_remote_report(report_path)
    result, repository, url, number = _validate_report_identity(
        payload,
        allowed_repositories,
    )
    if str(result.get("status")) != "draft_validated":
        raise RuntimeError("only a draft_validated Campaign may be promoted")
    if not bool(result.get("draft", False)):
        raise RuntimeError("remote Campaign report no longer describes a Draft PR")
    branch = str(result.get("branch") or "")
    commit_sha = str(result.get("commit_sha") or "")
    control = client or GitHubPullRequestControlClient()
    live = control.inspect(repository, url)
    _validate_live_identity(
        live,
        repository=repository,
        url=url,
        branch=branch,
        commit_sha=commit_sha,
    )
    if not live.draft:
        raise RuntimeError("Pull Request is already marked ready")
    if not _checks_passed(live.checks):
        raise RuntimeError("Pull Request CI checks are not all successful")
    if live.review_decision not in {"APPROVED", "REVIEW_REQUIRED"}:
        raise RuntimeError(
            "Pull Request review state does not permit controlled promotion"
        )
    control.mark_ready(repository, url)
    return RemoteCampaignControlResult(
        action="promote_ready",
        status="ready_for_human_merge",
        repository=repository,
        pull_request_url=url,
        pull_request_number=number,
        branch=branch,
        commit_sha=commit_sha,
        automatic_merge=False,
    )


def cleanup_remote_e2e_pull_request(
    report_path: Path,
    *,
    confirmation: str,
    allowed_repositories: tuple[str, ...],
    client: RemotePullRequestControlClient | None = None,
) -> RemoteCampaignControlResult:
    """Close a known E2E PR and delete only its protected test branch."""

    if confirmation != CLEANUP_CONFIRMATION:
        raise RuntimeError("E2E cleanup confirmation mismatch")
    payload = _load_remote_report(report_path)
    result, repository, url, number = _validate_report_identity(
        payload,
        allowed_repositories,
    )
    branch = str(result.get("branch") or "")
    commit_sha = str(result.get("commit_sha") or "")
    control = client or GitHubPullRequestControlClient()
    live = control.inspect(repository, url)
    _validate_live_identity(
        live,
        repository=repository,
        url=url,
        branch=branch,
        commit_sha=commit_sha,
    )
    control.close_and_delete_branch(repository, url)
    return RemoteCampaignControlResult(
        action="cleanup",
        status="closed_and_branch_deleted",
        repository=repository,
        pull_request_url=url,
        pull_request_number=number,
        branch=branch,
        commit_sha=commit_sha,
        automatic_merge=False,
    )
