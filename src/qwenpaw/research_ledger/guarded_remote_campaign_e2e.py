# -*- coding: utf-8 -*-
"""Hardened public entrypoint for remote Draft PR Campaign verification."""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from dataclasses import asdict, replace
from pathlib import Path
from typing import Sequence

from .change_request_delivery import ChangeRequest, ChangeRequestResult
from .delivery_lifecycle import DeliveryStatus
from .episode_package import EpisodePackage
from .github_delivery_monitor import GitHubDeliverySnapshot
from .remote_campaign_e2e import (
    DEFAULT_REQUIRED_LABEL,
    REMOTE_CONFIRMATION,
    DraftPullRequestProvider,
    GitHubDraftPullRequestProvider,
    RemoteCampaignE2EResult,
    RemoteE2EPolicy,
    RemoteMonitorAttempt,
    RunProcess,
    Sleep,
    SnapshotFetcher,
    _default_snapshot_fetcher,
    _render_remote_markdown,
    _run_process,
    _safe_component,
    run_remote_draft_pr_e2e,
)

_PULL_REQUEST_RE = re.compile(
    r"^https://github\.com/(?P<repository>[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)"
    r"/pull/(?P<number>\d+)(?:$|[/?#])"
)
_BRANCH_PREFIX = "autoresearch/e2e/"


class RepositoryBoundDraftProvider:
    """Require a Draft PR URL that belongs to the intended repository."""

    def __init__(
        self,
        repository: str,
        delegate: DraftPullRequestProvider,
    ) -> None:
        self.repository = repository
        self.delegate = delegate

    def create(self, request: ChangeRequest) -> ChangeRequestResult:
        if not request.draft:
            raise RuntimeError("guarded remote E2E refuses non-draft requests")
        if request.repository.casefold() != self.repository.casefold():
            raise RuntimeError("Draft PR request repository changed unexpectedly")
        result = self.delegate.create(request)
        match = _PULL_REQUEST_RE.match(result.url.strip())
        if match is None:
            raise RuntimeError("Draft PR provider returned a non-GitHub PR URL")
        if match.group("repository").casefold() != self.repository.casefold():
            raise RuntimeError("Draft PR URL repository does not match the Episode")
        number = int(match.group("number"))
        if result.number is not None and result.number != number:
            raise RuntimeError("Draft PR URL and provider number disagree")
        return ChangeRequestResult(url=result.url, number=number)


async def _branch_must_not_exist(
    repository_root: Path,
    branch: str,
    run_process: RunProcess,
) -> None:
    output = await run_process(
        [
            "git",
            "ls-remote",
            "--heads",
            "origin",
            f"refs/heads/{branch}",
        ],
        cwd=repository_root,
        timeout=120,
    )
    if output.strip():
        raise RuntimeError(
            "remote E2E branch already exists; refusing to overwrite it"
        )


async def _cleanup_local_verification(
    repository_root: Path,
    report_dir: Path,
    run_id: str,
    branch: str,
    run_process: RunProcess,
) -> None:
    worktree = report_dir / "local" / f"{_safe_component(run_id)}-worktree"
    if worktree.exists():
        try:
            await run_process(
                ["git", "worktree", "remove", "--force", str(worktree)],
                cwd=repository_root,
                timeout=120,
            )
        except Exception:
            pass
    try:
        branches = await run_process(
            ["git", "branch", "--list", branch],
            cwd=repository_root,
            timeout=30,
        )
        if branches.strip():
            await run_process(
                ["git", "branch", "-D", branch],
                cwd=repository_root,
                timeout=30,
            )
    except Exception:
        pass


def _draft_validated_result(
    result: RemoteCampaignE2EResult,
) -> RemoteCampaignE2EResult:
    if result.status != DeliveryStatus.MERGE_READY.value:
        return result
    attempts = list(result.monitor_attempts)
    if attempts:
        last = attempts[-1]
        attempts[-1] = replace(
            last,
            status="draft_validated",
            blockers=tuple(
                dict.fromkeys(
                    (*last.blockers, "draft_pr_requires_human_ready")
                )
            ),
        )
    return replace(
        result,
        status="draft_validated",
        monitor_attempts=tuple(attempts),
    )


def _rewrite_reports(result: RemoteCampaignE2EResult) -> None:
    json_path = Path(result.remote_report_json)
    if json_path.is_file():
        payload = json.loads(json_path.read_text(encoding="utf-8"))
        payload["result"] = result.to_dict()
        json_path.write_text(
            json.dumps(
                payload,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
    markdown_path = Path(result.remote_report_markdown)
    if markdown_path.is_file():
        payload = json.loads(json_path.read_text(encoding="utf-8"))
        issue_payload = payload.get("issue") or {}
        from .remote_campaign_e2e import RemoteIssueEvidence

        issue = RemoteIssueEvidence(
            repository=str(issue_payload.get("repository") or result.repository),
            number=int(issue_payload.get("number") or result.issue_number),
            state=str(issue_payload.get("state") or "open"),
            labels=tuple(issue_payload.get("labels") or ()),
            title=str(issue_payload.get("title") or ""),
        )
        markdown_path.write_text(
            _render_remote_markdown(result, issue),
            encoding="utf-8",
        )


async def run_guarded_remote_draft_pr_e2e(
    repository_root: Path,
    episode_path: Path,
    patch_path: Path,
    report_dir: Path,
    *,
    base_branch: str,
    confirmation: str,
    policy: RemoteE2EPolicy,
    provider: DraftPullRequestProvider | None = None,
    run_process: RunProcess = _run_process,
    snapshot_fetcher: SnapshotFetcher = _default_snapshot_fetcher,
    sleep: Sleep = asyncio.sleep,
    monitor_attempts: int = 1,
    monitor_interval_seconds: float = 30.0,
    keep_worktree: bool = False,
    issue=None,
) -> RemoteCampaignE2EResult:
    """Run the remote E2E with overwrite and evidence-consistency guards."""

    repository_root = repository_root.expanduser().resolve()
    episode_path = episode_path.expanduser().resolve()
    report_dir = report_dir.expanduser().resolve()
    episode = EpisodePackage.from_json(
        episode_path.read_text(encoding="utf-8")
    )
    branch = _BRANCH_PREFIX + _safe_component(episode.run_id)
    await _branch_must_not_exist(repository_root, branch, run_process)

    async def checked_snapshot_fetcher(
        repository: str,
        pr_url: str,
        worktree: Path,
        attempt: int,
    ) -> GitHubDeliverySnapshot:
        snapshot = await snapshot_fetcher(
            repository,
            pr_url,
            worktree,
            attempt,
        )
        expected = (
            await run_process(
                ["git", "rev-parse", "HEAD"],
                cwd=worktree,
                timeout=30,
            )
        ).strip()
        observed = snapshot.ci_report.commit_sha.strip()
        if not observed or observed.casefold() != expected.casefold():
            raise RuntimeError(
                "GitHub delivery HEAD does not match the host-verified commit"
            )
        return snapshot

    guarded_provider = RepositoryBoundDraftProvider(
        episode.repository,
        provider or GitHubDraftPullRequestProvider(),
    )
    try:
        result = await run_remote_draft_pr_e2e(
            repository_root,
            episode_path,
            patch_path,
            report_dir,
            base_branch=base_branch,
            confirmation=confirmation,
            policy=policy,
            issue=issue,
            provider=guarded_provider,
            run_process=run_process,
            snapshot_fetcher=checked_snapshot_fetcher,
            sleep=sleep,
            monitor_attempts=monitor_attempts,
            monitor_interval_seconds=monitor_interval_seconds,
            keep_worktree=keep_worktree,
        )
    except Exception:
        await _cleanup_local_verification(
            repository_root,
            report_dir,
            episode.run_id,
            branch,
            run_process,
        )
        raise
    result = _draft_validated_result(result)
    _rewrite_reports(result)
    return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Create one guarded Draft PR for a dedicated AutoResearch E2E Issue. "
            "The command never marks the PR ready and never merges it."
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
            run_guarded_remote_draft_pr_e2e(
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
                    "remote_confirmation_required": REMOTE_CONFIRMATION,
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
