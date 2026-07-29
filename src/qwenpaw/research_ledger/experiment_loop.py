"""Multi-round optimization loop for AutoResearch."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable

from .experiment_contract import ExperimentContract, ExperimentStatus


@dataclass(frozen=True)
class ExperimentRoundResult:
    round_index: int
    experiment: ExperimentContract


class ExperimentLoopController:
    def __init__(self, runner, registry):
        self.runner = runner
        self.registry = registry

    async def run_rounds(
        self,
        experiments: list[ExperimentContract],
        executor: Callable[[ExperimentContract], Awaitable[ExperimentContract]],
        max_rounds: int | None = None,
    ) -> list[ExperimentRoundResult]:
        results = []
        for index, experiment in enumerate(experiments[:max_rounds]):
            result = await self.runner.run(experiment, executor)
            results.append(ExperimentRoundResult(index, result))
        return results


def should_continue(result: ExperimentContract) -> bool:
    return result.status == ExperimentStatus.KEPT
