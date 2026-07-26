"""Headless AutoResearch CLI command."""

from __future__ import annotations

import asyncio
import json
from dataclasses import asdict
from pathlib import Path

import click

from ..research import run_research
from ..research_runtime import run_with_qwenpaw
from .task_cmd import _run_task


async def _run_with_qwenpaw(*args, **kwargs):
    """Compatibility wrapper with patchable dependencies for CLI tests."""
    return await run_with_qwenpaw(
        *args,
        **kwargs,
        _run_task_impl=_run_task,
        _research_runner=run_research,
    )


@click.command("research")
@click.argument(
    "task_dir",
    metavar="TASK_DIR",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
)
@click.option("--model", default=None, help="Model override, e.g. provider/model.")
@click.option("--rounds", default=3, type=click.IntRange(min=1), show_default=True)
@click.option("--max-iters", default=3, type=click.IntRange(min=1), show_default=True)
@click.option("--timeout", default=300, type=click.IntRange(min=1), show_default=True)
@click.option(
    "--judge-timeout", default=10.0, type=click.FloatRange(min=0, min_open=True)
)
@click.option("--min-improvement", default=0.0, type=click.FloatRange(min=0))
@click.option("--database-path", default=None, type=click.Path(path_type=Path))
@click.option("--agent-id", default="default", show_default=True)
def research_cmd(
    task_dir: Path,
    model: str | None,
    rounds: int,
    max_iters: int,
    timeout: int,
    judge_timeout: float,
    min_improvement: float,
    database_path: Path | None,
    agent_id: str,
) -> None:
    """Run iterative AutoResearch for TASK_DIR."""
    outcomes = asyncio.run(
        _run_with_qwenpaw(
            task_dir,
            model,
            rounds,
            max_iters,
            timeout,
            judge_timeout,
            min_improvement,
            database_path,
            agent_id,
        ),
    )
    click.echo(
        json.dumps([asdict(outcome) for outcome in outcomes], ensure_ascii=False)
    )
