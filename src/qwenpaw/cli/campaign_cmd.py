# -*- coding: utf-8 -*-
"""Operational CLI for local and guarded remote Issue Campaign verification."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import click

from ..research_ledger.guarded_remote_campaign_e2e import (
    REMOTE_CONFIRMATION,
    RemoteE2EPolicy,
    run_guarded_remote_draft_pr_e2e,
)
from ..research_ledger.local_campaign_verifier import verify_local_campaign
from ..research_ledger.remote_campaign_control import (
    CLEANUP_CONFIRMATION,
    PROMOTE_CONFIRMATION,
    cleanup_remote_e2e_pull_request,
    promote_remote_e2e_draft,
)


@click.group("campaign")
def campaign_cmd() -> None:
    """Verify and control AutoResearch Issue Campaign delivery flows."""


@campaign_cmd.command("local-verify")
@click.option(
    "--repository",
    required=True,
    type=click.Path(exists=True, file_okay=False, path_type=Path),
)
@click.option(
    "--episode",
    required=True,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
)
@click.option(
    "--patch",
    required=True,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
)
@click.option("--report-dir", required=True, type=click.Path(path_type=Path))
@click.option("--branch", default=None)
@click.option("--keep-worktree", is_flag=True, default=False)
def local_verify_cmd(
    repository: Path,
    episode: Path,
    patch: Path,
    report_dir: Path,
    branch: str | None,
    keep_worktree: bool,
) -> None:
    """Run the complete Campaign contract without any remote write."""

    result = asyncio.run(
        verify_local_campaign(
            repository,
            episode,
            patch,
            report_dir,
            branch=branch,
            keep_worktree=keep_worktree,
        )
    )
    click.echo(json.dumps(result.to_dict(), ensure_ascii=False))


@campaign_cmd.command("remote-e2e")
@click.option(
    "--repository",
    required=True,
    type=click.Path(exists=True, file_okay=False, path_type=Path),
)
@click.option(
    "--episode",
    required=True,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
)
@click.option(
    "--patch",
    required=True,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
)
@click.option("--report-dir", required=True, type=click.Path(path_type=Path))
@click.option("--base-branch", required=True)
@click.option(
    "--allow-repository",
    required=True,
    multiple=True,
    help="Exact owner/repository allowlist entry. Repeat to add entries.",
)
@click.option(
    "--required-label",
    default="autoresearch-e2e",
    show_default=True,
)
@click.option(
    "--confirm",
    required=True,
    help=f"Must equal {REMOTE_CONFIRMATION!r}.",
)
@click.option(
    "--monitor-attempts",
    default=1,
    type=click.IntRange(min=1, max=40),
    show_default=True,
)
@click.option(
    "--monitor-interval",
    default=30.0,
    type=click.FloatRange(min=0, max=3600),
    show_default=True,
)
@click.option("--keep-worktree", is_flag=True, default=False)
def remote_e2e_cmd(
    repository: Path,
    episode: Path,
    patch: Path,
    report_dir: Path,
    base_branch: str,
    allow_repository: tuple[str, ...],
    required_label: str,
    confirm: str,
    monitor_attempts: int,
    monitor_interval: float,
    keep_worktree: bool,
) -> None:
    """Push one protected branch and create one Draft PR; never merge it."""

    if confirm != REMOTE_CONFIRMATION:
        raise click.UsageError(
            "remote E2E confirmation mismatch; no remote write was performed"
        )
    policy = RemoteE2EPolicy(
        allowed_repositories=allow_repository,
        required_issue_label=required_label,
    )
    result = asyncio.run(
        run_guarded_remote_draft_pr_e2e(
            repository,
            episode,
            patch,
            report_dir,
            base_branch=base_branch,
            confirmation=confirm,
            policy=policy,
            monitor_attempts=monitor_attempts,
            monitor_interval_seconds=monitor_interval,
            keep_worktree=keep_worktree,
        )
    )
    click.echo(json.dumps(result.to_dict(), ensure_ascii=False))


@campaign_cmd.command("promote-ready")
@click.option(
    "--report",
    required=True,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
)
@click.option(
    "--allow-repository",
    required=True,
    multiple=True,
    help="Exact owner/repository allowlist entry. Repeat to add entries.",
)
@click.option(
    "--confirm",
    required=True,
    help=f"Must equal {PROMOTE_CONFIRMATION!r}.",
)
def promote_ready_cmd(
    report: Path,
    allow_repository: tuple[str, ...],
    confirm: str,
) -> None:
    """Mark one validated E2E Draft PR ready; never merge it."""

    if confirm != PROMOTE_CONFIRMATION:
        raise click.UsageError("Draft PR promotion confirmation mismatch")
    result = promote_remote_e2e_draft(
        report,
        confirmation=confirm,
        allowed_repositories=allow_repository,
    )
    click.echo(json.dumps(result.to_dict(), ensure_ascii=False))


@campaign_cmd.command("cleanup-e2e")
@click.option(
    "--report",
    required=True,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
)
@click.option(
    "--allow-repository",
    required=True,
    multiple=True,
    help="Exact owner/repository allowlist entry. Repeat to add entries.",
)
@click.option(
    "--confirm",
    required=True,
    help=f"Must equal {CLEANUP_CONFIRMATION!r}.",
)
def cleanup_e2e_cmd(
    report: Path,
    allow_repository: tuple[str, ...],
    confirm: str,
) -> None:
    """Close one known E2E PR and delete only its protected test branch."""

    if confirm != CLEANUP_CONFIRMATION:
        raise click.UsageError("E2E cleanup confirmation mismatch")
    result = cleanup_remote_e2e_pull_request(
        report,
        confirmation=confirm,
        allowed_repositories=allow_repository,
    )
    click.echo(json.dumps(result.to_dict(), ensure_ascii=False))
