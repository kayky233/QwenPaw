"""Execute experiment lifecycle around implementation and validation."""

from __future__ import annotations

from dataclasses import replace
from typing import Awaitable, Callable

from .experiment_contract import ExperimentContract, ExperimentStatus


class ExperimentRunner:
    def __init__(self, judge):
        self.judge = judge

    async def run(
        self,
        experiment: ExperimentContract,
        executor: Callable[[ExperimentContract], Awaitable[ExperimentContract]],
    ) -> ExperimentContract:
        running = replace(experiment, status=ExperimentStatus.RUNNING)
        result = await executor(running)
        return self.judge.evaluate(result)
