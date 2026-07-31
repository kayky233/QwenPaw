# -*- coding: utf-8 -*-
"""Create the isolated agent profiles required by direct Campaign runs."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import click
import httpx

from ..agents.tools.agent_management import create_agent_api_client


def _agent_map(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    agents = payload.get("agents", ())
    return {
        str(item.get("id")): item
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


def _wait_for_agents(
    client: httpx.Client,
    agent_ids: tuple[str, ...],
    *,
    timeout_seconds: float,
    poll_interval_seconds: float,
) -> dict[str, dict[str, Any]]:
    started = time.monotonic()
    while True:
        response = client.get("/agents")
        response.raise_for_status()
        raw = response.json()
        agents = _agent_map(raw if isinstance(raw, dict) else {})
        selected = {
            agent_id: agents.get(agent_id, {"id": agent_id, "startup_status": "missing"})
            for agent_id in agent_ids
        }
        statuses = {
            agent_id: str(item.get("startup_status") or "unknown").casefold()
            for agent_id, item in selected.items()
        }
        if all(status == "running" for status in statuses.values()):
            return selected
        failed = {
            agent_id: status
            for agent_id, status in statuses.items()
            if status in {"failed", "disabled", "missing"}
        }
        if failed:
            raise RuntimeError(
                "Campaign agent startup failed: "
                + ", ".join(
                    f"{agent_id}={status}" for agent_id, status in failed.items()
                )
            )
        if time.monotonic() - started >= timeout_seconds:
            raise TimeoutError(
                "Campaign agents did not become running within "
                f"{timeout_seconds:.0f} seconds: "
                + ", ".join(
                    f"{agent_id}={status}" for agent_id, status in statuses.items()
                )
            )
        time.sleep(poll_interval_seconds)


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
@click.option("--wait-timeout", default=120.0, type=click.FloatRange(min=1))
@click.option("--poll-interval", default=2.0, type=click.FloatRange(min=0.1))
def campaign_setup_cmd(
    api_url: str | None,
    implementer: str,
    reviewer: str,
    workspace_root: Path | None,
    language: str | None,
    wait_timeout: float,
    poll_interval: float,
) -> None:
    """Create missing Campaign agents and wait until both are running."""

    if not implementer.strip() or not reviewer.strip():
        raise click.UsageError("Campaign agent IDs cannot be blank")
    if implementer == reviewer:
        raise click.UsageError("Implementer and Reviewer must be different agents")

    try:
        with create_agent_api_client(api_url) as client:
            response = client.get("/agents")
            response.raise_for_status()
            raw = response.json()
            agents = _agent_map(raw if isinstance(raw, dict) else {})
            existing = set(agents)
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
            ready_agents = _wait_for_agents(
                client,
                (implementer, reviewer),
                timeout_seconds=wait_timeout,
                poll_interval_seconds=poll_interval,
            )
    except httpx.HTTPStatusError as exc:
        detail = exc.response.text.strip()
        raise click.ClickException(
            f"Agent API returned HTTP {exc.response.status_code}: {detail}"
        ) from exc
    except httpx.HTTPError as exc:
        raise click.ClickException(f"Agent API is unavailable: {exc}") from exc
    except (RuntimeError, TimeoutError) as exc:
        raise click.ClickException(str(exc)) from exc

    click.echo(
        json.dumps(
            {
                "ready": True,
                "implementer": implementer,
                "reviewer": reviewer,
                "created": created,
                "skipped_existing": skipped,
                "agents": ready_agents,
                "next": (
                    "qwenpaw campaign doctor "
                    f"--implementer {implementer} --reviewer {reviewer}"
                ),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
