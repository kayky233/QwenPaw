"""Experiment contracts for AutoResearch optimization loops."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ExperimentStatus(str, Enum):
    CREATED = "created"
    RUNNING = "running"
    PASSED = "passed"
    FAILED = "failed"
    KEPT = "kept"
    REVERTED = "reverted"


@dataclass(frozen=True)
class ExperimentContract:
    experiment_id: str
    run_id: str
    hypothesis: str
    change_summary: str
    metric_name: str
    baseline_metric: float | None = None
    candidate_metric: float | None = None
    status: ExperimentStatus = ExperimentStatus.CREATED

    def improvement(self) -> float | None:
        if self.baseline_metric is None or self.candidate_metric is None:
            return None
        if self.baseline_metric == 0:
            return None
        return (
            self.baseline_metric - self.candidate_metric
        ) / self.baseline_metric
