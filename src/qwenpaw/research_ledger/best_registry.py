"""Track best known experiment results."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BestExperiment:
    experiment_id: str
    metric_name: str
    value: float
    revision: str


class BestExperimentRegistry:
    def __init__(self):
        self._best: dict[str, BestExperiment] = {}

    def update(
        self,
        item: BestExperiment,
        lower_is_better: bool = True,
    ) -> bool:
        current = self._best.get(item.metric_name)
        if current is None:
            self._best[item.metric_name] = item
            return True

        improved = (
            item.value < current.value
            if lower_is_better
            else item.value > current.value
        )
        if improved:
            self._best[item.metric_name] = item
        return improved

    def get(self, metric_name: str):
        return self._best.get(metric_name)
