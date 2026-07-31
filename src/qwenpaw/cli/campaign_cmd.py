# -*- coding: utf-8 -*-
"""Operational CLI for direct and guarded Issue Campaign workflows."""

from __future__ import annotations

import asyncio
import json
import shutil
from dataclasses import replace
from pathlib import Path

import click

from ..research_ledger.campaign_api_client import (
    CampaignApiClient,
    CampaignApiError,
    write_campaign_report,
)
from ..research_ledger.campaign_manifest import (
    CampaignManifest,
    CampaignManifestCommand,
)
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
@click.option(
    "--api-url",
    default=None,
    help="QwenPaw API base URL; defaults to the root CLI host and port.",
)
@click.option("--agent-id", default="default", show_default=True)
@click.pass_context
def campaign_cmd(
    ctx: click.Context,
    api_url: str | None,
    agent_id: str,
) -> None:
    """Run and control AutoResearch Issue Campaigns."""

    ctx.ensure_object(dict)
    ctx.obj["campaign_api_url"] = api_url
    ctx.obj["campaign_agent_id"] = agent_id


def _client(ctx: click.Context) -> CampaignApiClient:
    root = ctx.find_root().obj or {}
    local = ctx.obj or {}
    api_url = local.get("campaign_api_url")
    if not api_url:
        host = root.get("host", "127.0.0.1")
        port = root.get("port", 8088)
        api_url = f"http://{host}:{port}/api"
    return CampaignApiClient(
        str(api_url),
        agent_id=str(local.get("campaign_agent_id") or "default"),
    )


def _manifest_from_options(
    *,
    issue_url: str,
    task_type: str,
    acceptance: tuple[str, ...],
    allowed: tuple[str, ...],
    frozen: tuple[str, ...],
    checks: tuple[str, ...],
    implementer: str,
    reviewer: str,
    max_attempts: int,
    delivery: str,
    session_id: str | None,
) -> CampaignManifest:
    criteria = acceptance or (
        "Resolve the linked issue without introducing regressions.",
    )
    commands = tuple(
        CampaignManifestCommand.from_shell(raw, index=index)
        for index, raw in enumerate(checks, start=1)
    )
    return CampaignManifest(
        issue_url=issue_url,
        task_type=task_type,
        acceptance_criteria=criteria,
        modifiable_files=allowed,
        frozen_files=frozen,
        commands=commands,
        implementer_agent_id=implementer,
        reviewer_agent_id=reviewer,
        max_attempts=max_attempts,
        delivery_mode=delivery,
        session_id=session_id,
    )


@campaign_cmd.command("doctor")
@click.option(
    "--delivery",
    type=click.Choice(["local", "draft_pr"]),
    default="local",
    show_default=True,
)
@click.option("--implementer", default="implementer", show_default=True)
@click.option("--reviewer", default="reviewer", show_default=True)
@click.pass_context
def doctor_cmd(
    ctx: click.Context,
    delivery: str,
    implementer: str,
    reviewer: str,
) -> None:
    """Check whether the server and selected agents can run a Campaign."""

    try:
        info = _client(ctx).info()
    except CampaignApiError as exc:
        raise click.ClickException(str(exc)) from exc
    agents = {
        str(item.get("id", "")): item
        for item in info.get("agents", ())
        if isinstance(item, dict)
    }
    problems: list[str] = []
    if not info.get("unsafe_execution_enabled"):
        problems.append("server local execution opt-in is disabled")
    if implementer not in agents:
        problems.append(f"implementer agent not found: {implementer}")
    if reviewer not in agents:
        problems.append(f"reviewer agent not found: {reviewer}")
    if implementer == reviewer:
        problems.append("implementer and reviewer must be different")
    if delivery == "draft_pr" and shutil.which("gh") is None:
        problems.append("draft_pr delivery requires the gh CLI")

    click.echo(
        json.dumps(
            {
                "ready": not problems,
                "api": info,
                "selected": {
                    "delivery": delivery,
                    "implementer": implementer,
                    "reviewer": reviewer,
                },
                "problems": problems,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    if problems:
        raise click.ClickException("Campaign doctor found blocking problems")


@campaign_cmd.command("init")
@click.argument("issue_url")
@click.option(
    "--output",
    default="campaign.json",
    show_default=True,
    type=click.Path(path_type=Path),
)
@click.option(
    "--task-type",
    type=click.Choice(
        ["bug_fix", "feature", "refactor", "performance", "research"]
    ),
    default="bug_fix",
    show_default=True,
)
@click.option("--accept", "acceptance", multiple=True)
@click.option("--allow", "allowed", multiple=True)
@click.option("--freeze", "frozen", multiple=True)
@click.option("--check", "checks", multiple=True)
@click.option("--implementer", default="implementer", show_default=True)
@click.option("--reviewer", default="reviewer", show_default=True)
@click.option("--max-attempts", default=3, type=click.IntRange(1, 10))
@click.option(
    "--delivery",
    type=click.Choice(["local", "draft_pr"]),
    default="local",
    show_default=True,
)
def init_cmd(
    issue_url: str,
    output: Path,
    task_type: str,
    acceptance: tuple[str, ...],
    allowed: tuple[str, ...],
    frozen: tuple[str, ...],
    checks: tuple[str, ...],
    implementer: str,
    reviewer: str,
    max_attempts: int,
    delivery: str,
) -> None:
    """Create a reusable Campaign manifest from a GitHub Issue URL."""

    try:
        manifest = _manifest_from_options(
            issue_url=issue_url,
            task_type=task_type,
            acceptance=acceptance,
            allowed=allowed,
            frozen=frozen,
            checks=checks,
            implementer=implementer,
            reviewer=reviewer,
            max_attempts=max_attempts,
            delivery=delivery,
            session_id=None,
        )
        target = manifest.write(output)
    except (ValueError, OSError) as exc:
        raise click.ClickException(str(exc)) from exc
    ready = bool(manifest.modifiable_files and manifest.commands)
    click.echo(
        json.dumps(
            {
                "manifest": str(target),
                "ready": ready,
                "next": (
                    f"qwenpaw campaign run --config {target}"
                    if ready
                    else "Add modifiable_files and commands before running."
                ),
            },
            ensure_ascii=False,
        )
    )


@campaign_cmd.command("run")
@click.argument("issue_url", required=False)
@click.option(
    "--config",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
)
@click.option(
    "--task-type",
    type=click.Choice(
        ["bug_fix", "feature", "refactor", "performance", "research"]
    ),
    default=None,
)
@click.option("--accept", "acceptance", multiple=True)
@click.option("--allow", "allowed", multiple=True)
@click.option("--freeze", "frozen", multiple=True)
@click.option("--check", "checks", multiple=True)
@click.option("--implementer", default=None)
@click.option("--reviewer", default=None)
@click.option("--max-attempts", type=click.IntRange(1, 10), default=None)
@click.option(
    "--delivery",
    type=click.Choice(["local", "draft_pr"]),
    default=None,
)
@click.option("--session-id", default=None)
@click.option("--timeout", default=3600.0, type=click.FloatRange(min=1))
@click.option("--poll-interval", default=2.0, type=click.FloatRange(min=0.1))
@click.option(
    "--report",
    default="campaign-result.json",
    show_default=True,
    type=click.Path(path_type=Path),
)
@click.option("--wait/--no-wait", default=True, show_default=True)
@click.pass_context
def run_cmd(
    ctx: click.Context,
    issue_url: str | None,
    config: Path | None,
    task_type: str | None,
    acceptance: tuple[str, ...],
    allowed: tuple[str, ...],
    frozen: tuple[str, ...],
    checks: tuple[str, ...],
    implementer: str | None,
    reviewer: str | None,
    max_attempts: int | None,
    delivery: str | None,
    session_id: str | None,
    timeout: float,
    poll_interval: float,
    report: Path,
    wait: bool,
) -> None:
    """Run an Issue Campaign from one command or a reusable manifest."""

    try:
        if config is not None:
            if issue_url or acceptance or allowed or frozen or checks:
                raise ValueError(
                    "--config cannot be combined with Issue or scope/check options"
                )
            manifest = CampaignManifest.read(config)
            manifest = replace(
                manifest,
                task_type=task_type or manifest.task_type,
                implementer_agent_id=(
                    implementer or manifest.implementer_agent_id
                ),
                reviewer_agent_id=reviewer or manifest.reviewer_agent_id,
                max_attempts=max_attempts or manifest.max_attempts,
                delivery_mode=delivery or manifest.delivery_mode,
                session_id=session_id or manifest.session_id,
            )
        else:
            if not issue_url:
                raise ValueError("ISSUE_URL or --config is required")
            manifest = _manifest_from_options(
                issue_url=issue_url,
                task_type=task_type or "bug_fix",
                acceptance=acceptance,
                allowed=allowed,
                frozen=frozen,
                checks=checks,
                implementer=implementer or "implementer",
                reviewer=reviewer or "reviewer",
                max_attempts=max_attempts or 3,
                delivery=delivery or "local",
                session_id=session_id,
            )
        payload = manifest.to_api_payload()
        client = _client(ctx)
        started = client.start(payload)
    except (ValueError, OSError, CampaignApiError) as exc:
        raise click.ClickException(str(exc)) from exc

    campaign_id = str(started["campaign_id"])
    click.echo(
        f"Campaign {campaign_id} accepted; delivery={manifest.delivery_mode}"
    )
    if not wait:
        click.echo(json.dumps(started, ensure_ascii=False))
        return

    def display(event: dict[str, object]) -> None:
        phase = str(event.get("phase", "event"))
        detail = str(event.get("detail", ""))
        click.echo(f"[{phase}] {detail}")

    try:
        result = client.wait(
            campaign_id,
            timeout_seconds=timeout,
            poll_interval_seconds=poll_interval,
            on_event=display,
        )
        json_path, markdown_path = write_campaign_report(
            result.state,
            report,
            elapsed_seconds=result.elapsed_seconds,
        )
    except (CampaignApiError, TimeoutError, OSError) as exc:
        raise click.ClickException(str(exc)) from exc

    click.echo(f"JSON report: {json_path}")
    click.echo(f"Markdown report: {markdown_path}")
    click.echo(json.dumps(result.state, ensure_ascii=False))
    if result.state.get("status") != "delivered":
        raise click.ClickException(
            "Campaign did not reach delivered; inspect the generated report"
        )


@campaign_cmd.command("status")
@click.argument("campaign_id")
@click.option("--report", type=click.Path(path_type=Path), default=None)
@click.pass_context
def status_cmd(
    ctx: click.Context,
    campaign_id: str,
    report: Path | None,
) -> None:
    """Read current durable Campaign state."""

    try:
        state = _client(ctx).get(campaign_id)
        if report is not None:
            write_campaign_report(state, report)
    except (CampaignApiError, OSError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(json.dumps(state, ensure_ascii=False, indent=2))


@campaign_cmd.command("cancel")
@click.argument("campaign_id")
@click.pass_context
def cancel_cmd(ctx: click.Context, campaign_id: str) -> None:
    """Cancel one active Campaign."""

    try:
        state = _client(ctx).cancel(campaign_id)
    except CampaignApiError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(json.dumps(state, ensure_ascii=False, indent=2))


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
    """Run the low-level Campaign contract without any remote write."""

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
)
@click.option(
    "--required-label",
    default="autoresearch-e2e",
    show_default=True,
)
@click.option("--confirm", required=True)
@click.option("--monitor-attempts", default=1, type=click.IntRange(1, 40))
@click.option("--monitor-interval", default=30.0, type=click.FloatRange(0, 3600))
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
    result = asyncio.run(
        run_guarded_remote_draft_pr_e2e(
            repository,
            episode,
            patch,
            report_dir,
            base_branch=base_branch,
            confirmation=confirm,
            policy=RemoteE2EPolicy(
                allowed_repositories=allow_repository,
                required_issue_label=required_label,
            ),
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
@click.option("--allow-repository", required=True, multiple=True)
@click.option("--confirm", required=True)
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
@click.option("--allow-repository", required=True, multiple=True)
@click.option("--confirm", required=True)
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
