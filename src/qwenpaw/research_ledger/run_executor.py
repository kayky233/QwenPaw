"""Durable orchestration layer for AutoResearch runs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable

from .contracts import ResearchStepContract
from .step_runner import ResearchStepRunner
from .step_runtime_adapter import build_runtime_steps


@dataclass(frozen=True)
class ResearchRunPlan:
    run_id: str
    steps: tuple[ResearchStepContract, ...]


class ResearchRunExecutor:
    """Execute a sequence of ledger-backed research steps.

    The executor intentionally does not know about models or tools. Agents are
    supplied as step executors, allowing future multi-agent orchestration.
    """

    def __init__(self, runner: ResearchStepRunner):
        self.runner = runner

    async def execute(
        self,
        plan: ResearchRunPlan,
        executors: dict[str, Callable[[ResearchStepContract], Awaitable[list]]],
    ) -> list[ResearchStepContract]:
        results = []
        for step in plan.steps:
            executor = executors.get(step.step_type.value)
            if executor is None:
                results.append(step)
                continue
            result = await self.runner.execute(step, executor)
            results.append(result)
        return results


def default_step_types():
    return tuple(item.step_type for item in build_runtime_steps())
