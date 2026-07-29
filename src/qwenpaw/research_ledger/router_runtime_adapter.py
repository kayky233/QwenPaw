"""Bridge legacy router lifecycle into durable run execution."""

from __future__ import annotations

from dataclasses import dataclass

from .contracts import ResearchStepContract, ResearchStepType
from .run_executor import ResearchRunExecutor, ResearchRunPlan


@dataclass(frozen=True)
class ResearchRuntimeRequest:
    run_id: str
    goal: str


class ResearchRouterRuntimeAdapter:
    def __init__(self, executor: ResearchRunExecutor):
        self.executor = executor

    def build_plan(self, request: ResearchRuntimeRequest) -> ResearchRunPlan:
        steps = tuple(
            ResearchStepContract(
                step_id=f"{request.run_id}-{step.value}",
                run_id=request.run_id,
                step_type=step,
                index=index,
                executor=step.value,
            )
            for index, step in enumerate(
                [
                    ResearchStepType.DISCOVERY,
                    ResearchStepType.PLAN,
                    ResearchStepType.IMPLEMENT,
                    ResearchStepType.TEST,
                    ResearchStepType.REVIEW,
                    ResearchStepType.DELIVERY,
                ]
            )
        )
        return ResearchRunPlan(request.run_id, steps)
