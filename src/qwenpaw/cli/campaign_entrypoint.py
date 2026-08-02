"""Unified direct-use Campaign command entrypoint."""

from __future__ import annotations

import json
from pathlib import Path

import click

from ..research_ledger.campaign_api_client import (
    CampaignApiError,
    write_campaign_report,
)
from . import campaign_cmd as campaign_module
from .campaign_group_extensions import install_campaign_group_extensions


def _install_auto_revision(group: click.Group) -> None:
    command = group.commands.get("run")
    if command is None or getattr(command, "_auto_revision_installed", False):
        return
    original = command.callback
    if original is None:
        raise RuntimeError("Campaign run callback is unavailable")
    command.params.append(
        click.Option(
            ["--auto-revise-rounds"],
            default=0,
            type=click.IntRange(0, 10),
            show_default=True,
            help=(
                "After an initial needs_revision outcome, repair the same "
                "branch and PR for at most this many additional rounds."
            ),
        )
    )

    def callback(*args, **kwargs):
        auto_rounds = int(kwargs.pop("auto_revise_rounds", 0) or 0)
        try:
            return original(*args, **kwargs)
        except click.ClickException as initial_error:
            if auto_rounds <= 0:
                raise
            report = Path(kwargs.get("report") or "campaign-result.json")
            json_path = report.expanduser().resolve()
            if json_path.suffix.lower() != ".json":
                json_path = json_path / "campaign-result.json"
            try:
                state = json.loads(json_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                raise click.ClickException(
                    "Automatic revision could not load the initial Campaign report"
                ) from exc
            if state.get("status") != "needs_revision":
                raise initial_error
            campaign_id = str(state.get("campaign_id") or "")
            if not campaign_id:
                raise click.ClickException(
                    "Automatic revision report is missing campaign_id"
                )
            context = click.get_current_context()
            client = campaign_module._client(context)
            timeout = float(kwargs.get("timeout") or 3600.0)
            poll_interval = float(kwargs.get("poll_interval") or 2.0)

            def display(event: dict[str, object]) -> None:
                phase = str(event.get("phase", "event"))
                detail = str(event.get("detail", ""))
                click.echo(f"[{phase}] {detail}")

            for round_index in range(1, auto_rounds + 1):
                click.echo(
                    f"Starting automatic revision {round_index}/{auto_rounds} "
                    f"for Campaign {campaign_id}"
                )
                try:
                    client.revise(campaign_id)
                    result = client.wait(
                        campaign_id,
                        timeout_seconds=timeout,
                        poll_interval_seconds=poll_interval,
                        on_event=display,
                    )
                    state = result.state
                    write_campaign_report(
                        state,
                        report,
                        elapsed_seconds=result.elapsed_seconds,
                    )
                except (
                    CampaignApiError,
                    OSError,
                    TimeoutError,
                    ValueError,
                ) as exc:
                    raise click.ClickException(str(exc)) from exc
                if state.get("status") == "delivered":
                    click.echo(json.dumps(state, ensure_ascii=False))
                    return None
            raise click.ClickException(
                "Campaign still needs revision after automatic revision budget; "
                f"inspect {json_path}"
            )

    command.callback = callback
    command._auto_revision_installed = True


campaign_cmd = install_campaign_group_extensions(campaign_module.campaign_cmd)
_install_auto_revision(campaign_cmd)
