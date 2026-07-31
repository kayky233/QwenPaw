# -*- coding: utf-8 -*-
"""Guarded remote Draft PR end-to-end verification for AutoResearch.

The runner deliberately supports only a Draft Pull Request workflow. Remote
writes require an explicit repository allowlist, an exact confirmation token,
a dedicated issue label, a clean local repository, and a fixed E2E branch
prefix. No merge operation is exposed by this module.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Protocol, Sequence
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .change_request_delivery import ChangeRequest, ChangeRequestResult
from .delivery_lifecycle import CICheckStatus, DeliveryStatus
from .episode_package import EpisodePackage
from .github_delivery_monitor import (
    GitHubDeliverySnapshot,
    parse_github_delivery_snapshot,
)
from .local_campaign_verifier import (
    LocalCampaignVerificationResult,
    verify_local_campaign,
)

REMOTE_CONFIRMATION = "ENABLE_REMOTE_DRAFT_PR_E2E"
DEFAULT_REQUIRED_LABEL = "autoresearch-e2e"
_BRANCH_PREFIX = "autoresearch/e2e/"
_SAFE_COMPONENT_RE = re.compile(r"[^A-Za-z0-9._-]+")
_GITHUB_REMOTE_RE = re.compile(
    r"^(?:https://github\.com/|git@github\.com:|ssh://git@github\.com/)"
    r"(?P<owner>[A-Za-z0-9_.-]+)/(?P<repo>[A-Za-z0-9_.-]+?)(?:\.git)?/?$"
)

RunProcess = Callable[..., Awaitable[str]]
Sleep = Callable[[float], Awaitable[None]]
SnapshotFetcher = Callable[[str, str, Path, int], Awaitable[GitHubDeliverySnapshot]]


@dataclass(frozen=True)
class RemoteE2EPolicy:
    """Immutable safety policy for one remote E2E invocation."""

    allowed_repositories: tuple[str, ...]
    required_issue_label: str = DEFAULT_REQUIRED_LABEL
    confirmation_token: str = REMOTE_CONFIRMATION
    branch_prefix: str = _BRANCH_PREFIX
    draft_only: bool = True
    automatic_merge: bool = False

    def validate(self) -> None:
        normalized = tuple(
            item.strip().casefold() for item in self.allowed_repositories
        )
        if not normalized or any("/" not in item for item in normalized):
            raise ValueError("remote E2E requires an explicit repository allowlist")
        if len(set(normalized)) != len(normalized):
            raise ValueError("remote E2E repository allowlist contains duplicates")
        if not self.required_issue_label.strip():
            raise ValueError("remote E2E requires a dedicated issue label")
        if self.confirmation_token != REMOTE_CONFIRMATION:
            raise ValueError("remote E2E confirmation token cannot be weakened")
        if self.branch_prefix != _BRANCH_PREFIX:
            raise ValueError("remote E2E branch prefix cannot be changed")
        if not self.draft_only:
            raise ValueError("remote E2E only supports Draft Pull Requests")
        if self.automatic_merge:
            raise ValueError("remote E2E cannot enable automatic merge")


@dataclass(frozen=True)
class RemoteIssueEvidence:
    repository: str
    number: int
    state: str
    labels: tuple[str, ...]
    title: str


@dataclass(frozen=True)
class RemoteMonitorAttempt:
    attempt: int
    status: str
    review_decision: str
    commit_sha: str
    checks: tuple[dict[str, str], ...]
    blockers: tuple[str, ...] = ()


@dataclass(frozen=True)
class RemoteCampaignE2EResult:
    status: str
    repository: str
    issue_number: int
    branch: str
    commit_sha: str
    pull_request_url: str
    pull_request_number: int | None
    draft: bool
    automatic_merge: bool
    local_verification_report: str
    remote_report_json: str
    remote_report_markdown: str
    monitor_attempts: tuple[RemoteMonitorAttempt, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class DraftPullRequestProvider(Protocol):
    def create(self, request: ChangeRequest) -> ChangeRequestResult: ...


class GitHubDraftPullRequestProvider:
    """Create a Draft PR using the authenticated GitHub CLI only."""

    def __init__(self, *, timeout_seconds: int = 120) -> None:
        self.timeout_seconds = timeout_seconds

    def create(self, request: ChangeRequest) -> ChangeRequestResult:
        if not request.draft:
            raise ValueError("remote E2E refuses non-draft pull requests")
        if shutil.which("gh") is None:
            raise RuntimeError("remote E2E requires an authenticated gh CLI")
        completed = subprocess.run(
            [
                "gh",
                "pr",
                "create",
                "--repo",
                request.repository,
                "--base",
                request.base_branch,
                "--head",
                request.head_branch,
                "--title",
                request.title,
                "--body",
                request.body,
                "--draft",
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=self.timeout_seconds,
        )
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout).strip()
            raise RuntimeError(f"GitHub Draft PR creation failed: {detail}")
        url = completed.stdout.strip().splitlines()[-1].strip()
        if not url:
            raise RuntimeError("GitHub CLI did not return a Pull Request URL")
        match = re.search(r"/pull/(\d+)(?:$|[/?#])", url)
        return ChangeRequestResult(
            url=url,
            number=int(match.group(1)) if match else None,
        )


async def _run_process(
    argv: list[str],
    *,
    cwd: Path,
    timeout: int,
) -> str:
    def execute() -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            argv,
            cwd=cwd,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )

    completed = await asyncio.to_thread(execute)
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()
        raise RuntimeError(
            f"command failed ({completed.returncode}): {' '.join(argv)}\n{detail}"
        )
    return completed.stdout


def _safe_component(value: str) -> str:
    normalized = _SAFE_COMPONENT_RE.sub("-", value.strip()).strip("-.")
    return normalized[:48] or "run"


def _remote_repository(remote_url: str) -> str:
    match = _GITHUB_REMOTE_RE.fullmatch(remote_url.strip())
    if match is None:
        raise RuntimeError(
            "remote E2E requires origin to point to github.com over HTTPS or SSH"
        )
    return f"{match.group('owner')}/{match.group('repo')}"


def _validate_remote_write(
    *,
    policy: RemoteE2EPolicy,
    episode: EpisodePackage,
    issue: RemoteIssueEvidence,
    confirmation: str,
    origin_repository: str,
    base_branch: str,
    branch: str,
) -> None:
    policy.validate()
    repository = episode.repository.casefold()
    allowed = {item.casefold() for item in policy.allowed_repositories}
    if repository not in allowed:
        raise RuntimeError("episode repository is not in the remote E2E allowlist")
    if origin_repository.casefold() != repository:
        raise RuntimeError("origin repository does not match the Episode repository")
    if issue.repository.casefold() != repository:
        raise RuntimeError("Issue evidence repository does not match the Episode")
    if episode.issue_number is None or issue.number != episode.issue_number:
        raise RuntimeError("Issue evidence number does not match the Episode")
    if issue.state.casefold() != "open":
        raise RuntimeError("remote E2E requires an open dedicated test Issue")
    labels = {item.casefold() for item in issue.labels}
    if policy.required_issue_label.casefold() not in labels:
        raise RuntimeError(
            "remote E2E Issue is missing required label: "
            + policy.required_issue_label
        )
    if confirmation != policy.confirmation_token:
        raise RuntimeError(
            "remote E2E confirmation mismatch; no remote write was performed"
        )
    if not base_branch.strip() or base_branch in {"HEAD", "-"}:
        raise RuntimeError("remote E2E requires an explicit base branch")
    if not branch.startswith(policy.branch_prefix):
        raise RuntimeError("remote E2E branch does not use the protected prefix")
    if branch in {"main", "master", base_branch}:
        raise RuntimeError("remote E2E refuses protected or base branch names")


def _load_issue_from_github(
    repository: str,
    issue_number: int,
    *,
    token: str | None = None,
) -> RemoteIssueEvidence:
    request = Request(
        f"https://api.github.com/repos/{repository}/issues/{issue_number}",
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "QwenPaw-AutoResearch-Remote-E2E",
            **({"Authorization": f"Bearer {token}"} if token else {}),
        },
    )
    try:
        with urlopen(request, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        raise RuntimeError(
            f"GitHub Issue lookup failed with HTTP {exc.code}"
        ) from exc
    except (URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"GitHub Issue lookup failed: {type(exc).__name__}") from exc
    labels = tuple(
        str(item.get("name", ""))
        for item in payload.get("labels", ())
        if isinstance(item, dict) and str(item.get("name", "")).strip()
    )
    return RemoteIssueEvidence(
        repository=repository,
        number=int(payload.get("number") or issue_number),
        state=str(payload.get("state") or "unknown"),
        labels=labels,
        title=str(payload.get("title") or ""),
    )


async def _default_snapshot_fetcher(
    repository: str,
    pr_url: str,
    worktree: Path,
    attempt: int,
) -> GitHubDeliverySnapshot:
    raw = await _run_process(
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


def _monitor_attempt(
    snapshot: GitHubDeliverySnapshot,
    attempt: int,
) -> RemoteMonitorAttempt:
    checks = tuple(
        {
            "name": item.name,
            "status": item.status.value,
            "url": item.url,
            "summary": item.summary,
        }
        for item in snapshot.ci_report.checks
    )
    blockers: list[str] = []
    ci_status = snapshot.ci_report.status
    review_decision = snapshot.review_decision.upper()
    if ci_status == CICheckStatus.PENDING:
        status = DeliveryStatus.CI_WAITING.value
        blockers.append("ci_pending")
    elif ci_status in {CICheckStatus.FAILED, CICheckStatus.CANCELLED}:
        status = "needs_revision"
        blockers.append(f"ci_{ci_status.value}")
    elif review_decision == "APPROVED":
        status = DeliveryStatus.MERGE_READY.value
    elif review_decision == "CHANGES_REQUESTED":
        status = "needs_revision"
        blockers.append("changes_requested")
    else:
        status = DeliveryStatus.REVIEW_WAITING.value
        blockers.append("review_required")
    return RemoteMonitorAttempt(
        attempt=attempt,
        status=status,
        review_decision=review_decision,
        commit_sha=snapshot.ci_report.commit_sha,
        checks=checks,
        blockers=tuple(blockers),
    )


def _draft_body(
    episode: EpisodePackage,
    local: LocalCampaignVerificationResult,
) -> str:
    criteria = "\n".join(
        f"- [x] {criterion}" for criterion in episode.acceptance_criteria
    )
    return f"""## AutoResearch guarded remote E2E

Closes #{episode.issue_number}

- Episode: `{episode.episode_id}`
- Episode digest: `{episode.digest()}`
- Base revision: `{episode.base_revision}`
- Verified local commit: `{local.commit_sha}`
- Branch: `{local.branch}`
- Delivery mode: `draft_pr_e2e`
- Automatic merge: `disabled`

### Acceptance criteria

{criteria}

### Local verification report

`{local.report_markdown}`

> This Pull Request was created by the guarded AutoResearch remote E2E flow.
> It remains Draft and must not be merged automatically.
"""


def _render_remote_markdown(
    result: RemoteCampaignE2EResult,
    issue: RemoteIssueEvidence,
) -> str:
    attempts = "\n".join(
        "| {} | {} | {} | {} |".format(
            item.attempt,
            item.status,
            item.review_decision,
            ", ".join(item.blockers) or "none",
        )
        for item in result.monitor_attempts
    ) or "| - | not_polled | - | - |"
    return f"""# Remote Draft PR E2E Verification

## Result

- Status: `{result.status}`
- Repository: `{result.repository}`
- Issue: `#{result.issue_number}` — {issue.title}
- Branch: `{result.branch}`
- Commit: `{result.commit_sha}`
- Draft Pull Request: {result.pull_request_url}
- Draft: `yes`
- Automatic merge: `disabled`

## Safety gates

- Explicit repository allowlist: passed
- Dedicated Issue label: passed
- Exact confirmation token: passed
- Origin repository identity: passed
- Protected E2E branch prefix: passed
- Automatic merge API: not available

## Delivery monitoring

| Attempt | Status | Review decision | Blockers |
|---:|---|---|---|
{attempts}
"""


async def run_remote_draft_pr_e2e(
    repository_root: Path,
    episode_path: Path,
    patch_path: Path,
    report_dir: Path,
    *,
    base_branch: str,
    confirmation: str,
    policy: RemoteE2EPolicy,
    issue: RemoteIssueEvidence | None = None,
    provider: DraftPullRequestProvider | None = None,
    run_process: RunProcess = _run_process,
    snapshot_fetcher: SnapshotFetcher = _default_snapshot_fetcher,
    sleep: Sleep = asyncio.sleep,
    monitor_attempts: int = 1,
    monitor_interval_seconds: float = 30.0,
    keep_worktree: bool = False,
) -> RemoteCampaignE2EResult:
    """Verify locally, push one protected branch, and create one Draft PR."""

    repository_root = repository_root.expanduser().resolve()
    episode_path = episode_path.expanduser().resolve()
    patch_path = patch_path.expanduser().resolve()
    report_dir = report_dir.expanduser().resolve()
    episode = EpisodePackage.from_json(
        episode_path.read_text(encoding="utf-8")
    )
    if episode.issue_number is None:
        raise RuntimeError("remote E2E requires an Episode linked to an Issue")
    if monitor_attempts < 1 or monitor_attempts > 40:
        raise ValueError("monitor_attempts must be between 1 and 40")
    if monitor_interval_seconds < 0 or monitor_interval_seconds > 3600:
        raise ValueError("monitor_interval_seconds must be between 0 and 3600")

    branch = _BRANCH_PREFIX + _safe_component(episode.run_id)
    remote_url = (
        await run_process(
            ["git", "remote", "get-url", "origin"],
            cwd=repository_root,
            timeout=30,
        )
    ).strip()
    origin_repository = _remote_repository(remote_url)
    issue_evidence = issue or await asyncio.to_thread(
        _load_issue_from_github,
        episode.repository,
        episode.issue_number,
        token=os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN"),
    )
    _validate_remote_write(
        policy=policy,
        episode=episode,
        issue=issue_evidence,
        confirmation=confirmation,
        origin_repository=origin_repository,
        base_branch=base_branch,
        branch=branch,
    )

    local_report_dir = report_dir / "local"
    local = await verify_local_campaign(
        repository_root,
        episode_path,
        patch_path,
        local_report_dir,
        branch=branch,
        keep_worktree=True,
    )
    worktree = Path(local.worktree)
    pushed = False
    pr_result: ChangeRequestResult | None = None
    attempts: list[RemoteMonitorAttempt] = []
    try:
        await run_process(
            ["git", "push", "--set-upstream", "origin", branch],
            cwd=worktree,
            timeout=600,
        )
        pushed = True
        request = ChangeRequest(
            repository=episode.repository,
            base_branch=base_branch,
            head_branch=branch,
            title=f"test(autoresearch): remote E2E for issue #{episode.issue_number}",
            body=_draft_body(episode, local),
            draft=True,
        )
        pr_result = await asyncio.to_thread(
            (provider or GitHubDraftPullRequestProvider()).create,
            request,
        )
        if not pr_result.url.strip():
            raise RuntimeError("Draft PR provider returned an empty URL")

        for attempt in range(1, monitor_attempts + 1):
            snapshot = await snapshot_fetcher(
                episode.repository,
                pr_result.url,
                worktree,
                attempt,
            )
            item = _monitor_attempt(snapshot, attempt)
            attempts.append(item)
            if item.status in {
                DeliveryStatus.MERGE_READY.value,
                "needs_revision",
            }:
                break
            if attempt < monitor_attempts:
                await sleep(monitor_interval_seconds)

        final_status = (
            attempts[-1].status
            if attempts
            else DeliveryStatus.DRAFT_PR.value
        )
        result = RemoteCampaignE2EResult(
            status=final_status,
            repository=episode.repository,
            issue_number=episode.issue_number,
            branch=branch,
            commit_sha=local.commit_sha,
            pull_request_url=pr_result.url,
            pull_request_number=pr_result.number,
            draft=True,
            automatic_merge=False,
            local_verification_report=local.report_markdown,
            remote_report_json=str(report_dir / "remote-verification.json"),
            remote_report_markdown=str(report_dir / "remote-verification.md"),
            monitor_attempts=tuple(attempts),
        )
        report_dir.mkdir(parents=True, exist_ok=True)
        Path(result.remote_report_json).write_text(
            json.dumps(
                {
                    "result": result.to_dict(),
                    "policy": asdict(policy),
                    "issue": asdict(issue_evidence),
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        Path(result.remote_report_markdown).write_text(
            _render_remote_markdown(result, issue_evidence),
            encoding="utf-8",
        )
        return result
    except Exception:
        if pushed and pr_result is None:
            try:
                await run_process(
                    ["git", "push", "origin", "--delete", branch],
                    cwd=worktree,
                    timeout=120,
                )
            except Exception:
                pass
        raise
    finally:
        if not keep_worktree:
            await run_process(
                ["git", "worktree", "remove", "--force", str(worktree)],
                cwd=repository_root,
                timeout=120,
            )
            await run_process(
                ["git", "branch", "-D", branch],
                cwd=repository_root,
                timeout=30,
            )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run a guarded AutoResearch remote Draft PR E2E. This command "
            "pushes one protected branch and creates one Draft PR. It never merges."
        )
    )
    parser.add_argument("--repository", required=True, type=Path)
    parser.add_argument("--episode", required=True, type=Path)
    parser.add_argument("--patch", required=True, type=Path)
    parser.add_argument("--report-dir", required=True, type=Path)
    parser.add_argument("--base-branch", required=True)
    parser.add_argument("--allow-repository", required=True, action="append")
    parser.add_argument("--confirm", required=True)
    parser.add_argument("--required-label", default=DEFAULT_REQUIRED_LABEL)
    parser.add_argument("--monitor-attempts", type=int, default=1)
    parser.add_argument("--monitor-interval", type=float, default=30.0)
    parser.add_argument("--keep-worktree", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    policy = RemoteE2EPolicy(
        allowed_repositories=tuple(args.allow_repository),
        required_issue_label=args.required_label,
    )
    try:
        result = asyncio.run(
            run_remote_draft_pr_e2e(
                args.repository,
                args.episode,
                args.patch,
                args.report_dir,
                base_branch=args.base_branch,
                confirmation=args.confirm,
                policy=policy,
                monitor_attempts=args.monitor_attempts,
                monitor_interval_seconds=args.monitor_interval,
                keep_worktree=args.keep_worktree,
            )
        )
    except Exception as exc:
        print(
            json.dumps(
                {
                    "status": "failed",
                    "error": f"{type(exc).__name__}: {exc}",
                    "automatic_merge": False,
                },
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        return 1
    print(json.dumps(result.to_dict(), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
