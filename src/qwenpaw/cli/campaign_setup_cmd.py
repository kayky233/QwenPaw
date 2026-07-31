# -*- coding: utf-8 -*-
"""Create the isolated agent profiles required by direct Campaign runs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import click
import httpx

from ..agents.tools.agent_management import create_agent_api_client


def _agent_ids(payload: dict[str, Any]) -> set[str]:
    agents = payload.get("agents", ())
    return {
        str(item.get("id"))
        for item in agents
        if isinstance(item, dict) and str(item.get("id", "")).strip()
    }


def _create_agent(
    client: httpx.Client,
    *,
    agent_id: str,
    name: str,
    description: str,
    workspace_root: Path | None,
    language: str | None,
) -> dict[str, Any]:
    workspace = (
        str((workspace_root / agent_id).expanduser().resolve())
        if workspace_root is not None
        else None
    )
    payload: dict[str, Any] = {
        "id": agent_id,
        "name": name,
        "description": description,
    }
    if workspace is not None:
        payload["workspace_dir"] = workspace
    if language is not None:
        payload["language"] = language
    response = client.post("/agents", json=payload)
    response.raise_for_status()
    result = response.json()
    return result if isinstance(result, dict) else {"id": agent_id}


@click.command("campaign-setup")
@click.option("--api-url", default=None)
@click.option("--implementer", default="implementer", show_default=True)
@click.option("--reviewer", default="reviewer", show_default=True)
@click.option(
    "--workspace-root",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
)
@click.option("--language", default=None)
def campaign_setup_cmd(
    api_url: str | None,
    implementer: str,
    reviewer: str,
    workspace_root: Path | None,
    language: str | None,
) -> None:
    """Create missing Campaign agents without overwriting existing profiles."""

    if not implementer.strip() or not reviewer.strip():
        raise click.UsageError("Campaign agent IDs cannot be blank")
    if implementer == reviewer:
        raise click.UsageError("Implementer and Reviewer must be different agents")

    try:
        with create_agent_api_client(api_url) as client:
            response = client.get("/agents")
            response.raise_for_status()
            raw = response.json()
            agents = raw if isinstance(raw, dict) else {}
            existing = _agent_ids(agents)
            created: list[dict[str, Any]] = []
            skipped: list[str] = []
            if implementer in existing:
                skipped.append(implementer)
            else:
                created.append(
                    _create_agent(
                        client,
                        agent_id=implementer,
                        name="AutoResearch Implementer",
                        description=(
                            "Writes code only inside host-approved Campaign "
                            "worktrees and file scope."
                        ),
                        workspace_root=workspace_root,
                        language=language,
                    )
                )
            if reviewer in existing:
                skipped.append(reviewer)
            else:
                created.append(
                    _create_agent(
                        client,
                        agent_id=reviewer,
                        name="AutoResearch Reviewer",
                        description=(
                            "Independently reviews host-verified Campaign diff "
                            "and validation evidence without modifying it."
                        ),
                        workspace_root=workspace_root,
                        language=language,
                    )
                )
    except httpx.HTTPStatusError as exc:
        detail = exc.response.text.strip()
        raise click.ClickException(
            f"Agent API returned HTTP {exc.response.status_code}: {detail}"
        ) from exc
    except httpx.HTTPError as exc:
        raise click.ClickException(f"Agent API is unavailable: {exc}") from exc

    click.echo(
        json.dumps(
            {
                "ready": True,
                "implementer": implementer,
                "reviewer": reviewer,
                "created": created,
                "skipped_existing": skipped,
                "next": (
                    "qwenpaw campaign doctor "
                    f"--implementer {implementer} --reviewer {reviewer}"
                ),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
