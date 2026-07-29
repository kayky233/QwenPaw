"""Execution primitives for AutoResearch step contracts.

The runner intentionally owns lifecycle bookkeeping only. Concrete agents and
sandbox executors are injected by callers.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Awaitable, Callable

from .contracts import (
    ResearchArtifactContract,
    ResearchArtifactType,
    ResearchStepContract,
    ResearchStepStatus,
    required_artifacts_for_step,
)
from .step_repository import ResearchArtifactRepository, ResearchStepRepository


class StepBlockedError(RuntimeError):
    pass


class ResearchStepRunner:
    def __init__(
        self,
        steps: ResearchStepRepository,
        artifacts: ResearchArtifactRepository,
    ) -> None:
        self.steps = steps
        self.artifacts = artifacts

    async def execute(
        self,
        step: ResearchStepContract,
        executor: Callable[[ResearchStepContract], Awaitable[list[ResearchArtifactContract]]],
    ) -> ResearchStepContract:
        self.steps.create(replace(step, status=ResearchStepStatus.RUNNING))

        try:
            produced = await executor(step)
            for artifact in produced:
                self.artifacts.add(artifact)

            if not self.artifacts.all_verified(
                step.step_id,
                required_artifacts_for_step(step.step_type),
            ):
                raise StepBlockedError(
                    f"missing verified artifacts for {step.step_type.value}"
                )

            return self.steps.update_status(
                step.step_id,
                ResearchStepStatus.COMPLETED,
            )
        except StepBlockedError:
            return self.steps.update_status(
                step.step_id,
                ResearchStepStatus.BLOCKED,
            )
        except Exception:
            return self.steps.update_status(
                step.step_id,
                ResearchStepStatus.FAILED,
            )
