"""Bridge the QwenPaw agent runtime to the pure AutoResearch engine."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from collections.abc import Awaitable
from pathlib import Path
from typing import Any

from .research import ResearchOutcome, run_research


async def run_with_qwenpaw(
    task_dir: Path,
    model: str | None,
    rounds: int,
    max_iters: int,
    timeout: int,
    judge_timeout: float,
    min_improvement: float,
    database_path: Path | None,
    agent_id: str,
    *,
    on_outcome: Callable[[ResearchOutcome], None] | None = None,
    on_progress: Callable[[tuple[int, str, str]], None] | None = None,
    is_cancelled: Callable[[], bool] | None = None,
    _run_task_impl: Callable[..., Awaitable[dict[str, Any]]] | None = None,
    _research_runner: (
        Callable[..., Awaitable[tuple[ResearchOutcome, ...]]] | None
    ) = None,
) -> tuple[ResearchOutcome, ...]:
    """Run AutoResearch using the configured QwenPaw agent as proposer."""
    from .cli.task_cmd import _run_task
    from .config.config import ModelSlotConfig, load_agent_config

    run_task = _run_task_impl or _run_task
    research_runner = _research_runner or run_research
    agent_config = load_agent_config(agent_id)
    if model:
        provider_id, separator, model_id = model.partition("/")
        agent_config.active_model = ModelSlotConfig(
            provider_id=provider_id if separator else "",
            model=model_id if separator else provider_id,
        )

    async def proposer(prompt: str) -> str:
        result = await run_task(
            instruction=prompt,
            agent_config=agent_config,
            request_context={
                "session_id": f"research-{uuid.uuid4().hex[:12]}",
                "user_id": "research",
                "channel": "console",
                "agent_id": agent_id,
            },
            max_iters=max_iters,
            timeout=timeout,
            output_dir=None,
        )
        if result["status"] != "success":
            detail = result.get("error") or result["status"]
            raise RuntimeError(f"research proposer failed: {detail}")
        return result.get("response", "")

    return await research_runner(
        task_dir,
        proposer,
        rounds,
        database_path=database_path,
        judge_timeout=judge_timeout,
        min_improvement=min_improvement,
        on_outcome=on_outcome,
        on_progress=on_progress,
        is_cancelled=is_cancelled,
    )


# Backward-compatible name used by the existing router/tests.
_run_with_qwenpaw = run_with_qwenpaw
