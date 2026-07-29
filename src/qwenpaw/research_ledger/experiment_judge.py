"""Judge experiment results and decide keep or revert."""

from __future__ import annotations

from dataclasses import replace

from .experiment_contract import ExperimentContract, ExperimentStatus


class ExperimentJudge:
    def __init__(self, minimum_improvement: float = 0.0):
        self.minimum_improvement = minimum_improvement

    def evaluate(self, experiment: ExperimentContract) -> ExperimentContract:
        improvement = experiment.improvement()
        if improvement is None:
            return replace(experiment, status=ExperimentStatus.FAILED)

        if improvement > self.minimum_improvement:
            return replace(experiment, status=ExperimentStatus.KEPT)

        return replace(experiment, status=ExperimentStatus.REVERTED)
