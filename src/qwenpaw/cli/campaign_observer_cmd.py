# -*- coding: utf-8 -*-
"""Read-only CLI commands for Campaign history and delivery observation."""

from __future__ import annotations

import json
from pathlib import Path

import click

from ..research_ledger.campaign_api_client import (
    CampaignApiClient,
    CampaignApiError,
    write_campaign_report,
)


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

    def display(event: dict[str, object]) -> None:
        click.echo(
            "[{}] {}".format(
                str(event.get("phase", "event")),
                str(event.get("detail", "")),
            )
        )

    try:
        result = _client(ctx, api_url, agent_id).wait(
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
