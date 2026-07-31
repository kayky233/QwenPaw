# -*- coding: utf-8 -*-
"""CLI controls for applying and cleaning local Campaign results."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

import click

from ..research_ledger.campaign_api_client import (
    CampaignApiClient,
    CampaignApiError,
)
from ..research_ledger.campaign_local_control import (
    APPLY_CONFIRMATION,
    CLEANUP_LOCAL_CONFIRMATION,
    apply_local_campaign_patch,
    cleanup_local_campaign,
)


@click.group("campaign-local")
@click.option("--api-url", default=None)
@click.option("--agent-id", default="default", show_default=True)
@click.pass_context
def campaign_local_cmd(
    ctx: click.Context,
    api_url: str | None,
    agent_id: str,
) -> None:
    """Apply or clean a verified local Campaign result."""

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


@campaign_local_cmd.command("apply")
@click.argument("campaign_id")
@click.option(
    "--repository",
    default=".",
    show_default=True,
    type=click.Path(exists=True, file_okay=False, path_type=Path),
)
@click.option(
    "--confirm",
    required=True,
    help=f"Must equal {APPLY_CONFIRMATION!r}.",
)
@click.pass_context
def apply_cmd(
    ctx: click.Context,
    campaign_id: str,
    repository: Path,
    confirm: str,
) -> None:
    """Apply and stage a verified Campaign patch; do not commit it."""

    if confirm != APPLY_CONFIRMATION:
        raise click.UsageError("local Campaign apply confirmation mismatch")
    try:
        state = _client(ctx).get(campaign_id)
        result = apply_local_campaign_patch(
            state,
            repository,
            confirmation=confirm,
        )
    except (CampaignApiError, RuntimeError, OSError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(json.dumps(asdict(result), ensure_ascii=False, indent=2))


@campaign_local_cmd.command("cleanup")
@click.argument("campaign_id")
@click.option(
    "--confirm",
    required=True,
    help=f"Must equal {CLEANUP_LOCAL_CONFIRMATION!r}.",
)
@click.pass_context
def cleanup_cmd(
    ctx: click.Context,
    campaign_id: str,
    confirm: str,
) -> None:
    """Remove the preserved Campaign worktree and its temporary branch."""

    if confirm != CLEANUP_LOCAL_CONFIRMATION:
        raise click.UsageError("local Campaign cleanup confirmation mismatch")
    try:
        state = _client(ctx).get(campaign_id)
        result = cleanup_local_campaign(state, confirmation=confirm)
    except (CampaignApiError, RuntimeError, OSError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(json.dumps(asdict(result), ensure_ascii=False, indent=2))
