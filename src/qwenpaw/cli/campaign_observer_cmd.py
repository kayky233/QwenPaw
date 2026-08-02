# -*- coding: utf-8 -*-
"""Read-only and recovery CLI commands for durable Issue Campaigns."""

from __future__ import annotations

import json
from pathlib import Path

import click

from ..research_ledger.campaign_api_client import (
    CampaignApiClient,
    CampaignApiError,
    write_campaign_report,
)

WORKTREE_CLEANUP_CONFIRMATION = "CLEANUP_AUTORESEARCH_CAMPAIGN_WORKTREE"


def _client(
    ctx: click.Context,
    api_url: str | None,
    agent_id: str,
) -> CampaignApiClient:
    root = ctx.find_root().obj or {}
    if not api_url:
        host = root.get("host", "127.0.0.1")
        port = root.get("port", 8088)
        api_url = f"http://{host}:{port}/api"
    return CampaignApiClient(api_url, agent_id=agent_id)


def _display(event: dict[str, object]) -> None:
    click.echo(
        "[{}] {}".format(
            str(event.get("phase", "event")),
            str(event.get("detail", "")),
        )
    )


@click.command("campaign-history")
@click.option("--api-url", default=None)
@click.option("--agent-id", default="default", show_default=True)
@click.option("--limit", default=20, type=click.IntRange(1, 200))
@click.option("--status", default=None)
@click.pass_context
def campaign_history_cmd(
    ctx: click.Context,
    api_url: str | None,
    agent_id: str,
    limit: int,
    status: str | None,
) -> None:
    """List durable Campaign runs owned by the current agent and user."""

    try:
        payload = _client(ctx, api_url, agent_id).history(
            limit=limit,
            status=status,
        )
    except (CampaignApiError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(json.dumps(payload, ensure_ascii=False, indent=2))


@click.command("campaign-watch")
@click.argument("campaign_id")
@click.option("--api-url", default=None)
@click.option("--agent-id", default="default", show_default=True)
@click.option("--timeout", default=3600.0, type=click.FloatRange(min=1))
@click.option("--poll-interval", default=2.0, type=click.FloatRange(min=0.1))
@click.option(
    "--report",
    default="campaign-result.json",
    show_default=True,
    type=click.Path(path_type=Path),
)
@click.pass_context
def campaign_watch_cmd(
    ctx: click.Context,
    campaign_id: str,
    api_url: str | None,
    agent_id: str,
    timeout: float,
    poll_interval: float,
    report: Path,
) -> None:
    """Reconnect to an existing Campaign, wait, and regenerate its report."""

    try:
        result = _client(ctx, api_url, agent_id).wait(
            campaign_id,
            timeout_seconds=timeout,
            poll_interval_seconds=poll_interval,
            on_event=_display,
        )
        json_path, markdown_path = write_campaign_report(
            result.state,
            report,
            elapsed_seconds=result.elapsed_seconds,
        )
    except (CampaignApiError, TimeoutError, OSError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"JSON report: {json_path}")
    click.echo(f"Markdown report: {markdown_path}")
    click.echo(json.dumps(result.state, ensure_ascii=False))
    if result.state.get("status") != "delivered":
        raise click.ClickException(
            "Campaign did not reach delivered; inspect the generated report"
        )


@click.command("campaign-refresh")
@click.argument("campaign_id")
@click.option("--api-url", default=None)
@click.option("--agent-id", default="default", show_default=True)
@click.option(
    "--report",
    default="campaign-result.json",
    show_default=True,
    type=click.Path(path_type=Path),
)
@click.pass_context
def campaign_refresh_cmd(
    ctx: click.Context,
    campaign_id: str,
    api_url: str | None,
    agent_id: str,
    report: Path,
) -> None:
    """Refresh one Draft PR Campaign's CI and review evidence."""

    try:
        state = _client(ctx, api_url, agent_id).refresh_delivery(campaign_id)
        json_path, markdown_path = write_campaign_report(state, report)
    except (CampaignApiError, OSError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"JSON report: {json_path}")
    click.echo(f"Markdown report: {markdown_path}")
    click.echo(json.dumps(state, ensure_ascii=False, indent=2))
    if state.get("status") == "needs_revision":
        raise click.ClickException(
            "Draft PR delivery needs revision; inspect the generated report"
        )


@click.command("campaign-revise")
@click.argument("campaign_id")
@click.option("--api-url", default=None)
@click.option("--agent-id", default="default", show_default=True)
@click.option("--feedback", default="")
@click.option("--rounds", default=1, type=click.IntRange(1, 10), show_default=True)
@click.option("--timeout", default=3600.0, type=click.FloatRange(min=1))
@click.option("--poll-interval", default=2.0, type=click.FloatRange(min=0.1))
@click.option("--monitor-attempts", default=10, type=click.IntRange(1, 40))
@click.option("--monitor-interval", default=30.0, type=click.FloatRange(0, 3600))
@click.option(
    "--report",
    default="campaign-result.json",
    show_default=True,
    type=click.Path(path_type=Path),
)
@click.pass_context
def campaign_revise_cmd(
    ctx: click.Context,
    campaign_id: str,
    api_url: str | None,
    agent_id: str,
    feedback: str,
    rounds: int,
    timeout: float,
    poll_interval: float,
    monitor_attempts: int,
    monitor_interval: float,
    report: Path,
) -> None:
    """Revise the same Campaign branch and PR with a bounded round budget."""

    client = _client(ctx, api_url, agent_id)
    state: dict[str, object] = {}
    try:
        for round_index in range(1, rounds + 1):
            current = client.get(campaign_id)
            if current.get("status") == "delivered":
                state = current
                break
            click.echo(f"Starting revision round {round_index}/{rounds}")
            client.revise(
                campaign_id,
                feedback=feedback,
                monitor_attempts=monitor_attempts,
                monitor_interval_seconds=monitor_interval,
            )
            result = client.wait(
                campaign_id,
                timeout_seconds=timeout,
                poll_interval_seconds=poll_interval,
                on_event=_display,
            )
            state = result.state
            if state.get("status") == "delivered":
                break
        if not state:
            state = client.get(campaign_id)
        json_path, markdown_path = write_campaign_report(state, report)
    except (CampaignApiError, TimeoutError, OSError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"JSON report: {json_path}")
    click.echo(f"Markdown report: {markdown_path}")
    click.echo(json.dumps(state, ensure_ascii=False, indent=2))
    if state.get("status") != "delivered":
        raise click.ClickException(
            "Campaign still needs revision after the configured round budget"
        )


@click.command("campaign-recovery")
@click.argument("campaign_id")
@click.option("--api-url", default=None)
@click.option("--agent-id", default="default", show_default=True)
@click.pass_context
def campaign_recovery_cmd(
    ctx: click.Context,
    campaign_id: str,
    api_url: str | None,
    agent_id: str,
) -> None:
    """Show whether a failed or interrupted Campaign can revise or clean up."""

    try:
        payload = _client(ctx, api_url, agent_id).recovery(campaign_id)
    except CampaignApiError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(json.dumps(payload, ensure_ascii=False, indent=2))


@click.command("campaign-cleanup-worktree")
@click.argument("campaign_id")
@click.option("--api-url", default=None)
@click.option("--agent-id", default="default", show_default=True)
@click.option(
    "--confirm",
    required=True,
    help=f"Must equal {WORKTREE_CLEANUP_CONFIRMATION!r}.",
)
@click.pass_context
def campaign_cleanup_worktree_cmd(
    ctx: click.Context,
    campaign_id: str,
    api_url: str | None,
    agent_id: str,
    confirm: str,
) -> None:
    """Export uncommitted recovery evidence and delete only the local worktree."""

    if confirm != WORKTREE_CLEANUP_CONFIRMATION:
        raise click.UsageError("Campaign worktree cleanup confirmation mismatch")
    try:
        payload = _client(ctx, api_url, agent_id).cleanup_worktree(
            campaign_id,
            confirmation=confirm,
        )
    except CampaignApiError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(json.dumps(payload, ensure_ascii=False, indent=2))
