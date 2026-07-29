"""Crash recovery primitives for durable AutoResearch runs."""

from __future__ import annotations

from dataclasses import dataclass

from .contracts import ResearchStepStatus
from .step_repository import ResearchStepRepository


@dataclass(frozen=True)
class RecoveryDecision:
    step_id: str
    previous_status: ResearchStepStatus
    recovered_status: ResearchStepStatus
    reason: str


class ResearchRecoveryManager:
    def __init__(self, steps: ResearchStepRepository):
        self.steps = steps

    def recover_interrupted_steps(self, run_id: str) -> list[RecoveryDecision]:
        decisions: list[RecoveryDecision] = []
        for step in self.steps.list_for_run(run_id):
            if step.status != ResearchStepStatus.RUNNING:
                continue
            updated = self.steps.update_status(
                step.step_id,
                ResearchStepStatus.BLOCKED,
            )
            decisions.append(
                RecoveryDecision(
                    step_id=updated.step_id,
                    previous_status=step.status,
                    recovered_status=updated.status,
                    reason="runtime_interrupted_requires_review",
                )
            )
        return decisions
