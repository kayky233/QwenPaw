"""Execution primitives for AutoResearch step contracts."""

from __future__ import annotations

from dataclasses import replace
from typing import Awaitable, Callable

from .contracts import ResearchArtifactContract, ResearchStepContract, ResearchStepStatus, required_artifacts_for_step
from .event_store import ResearchEventStore
from .step_events import ARTIFACT_CREATED, STEP_BLOCKED, STEP_COMPLETED, STEP_FAILED, STEP_STARTED, ResearchStepEvent
from .step_repository import ResearchArtifactRepository, ResearchStepRepository


class StepBlockedError(RuntimeError):
    pass


class ResearchStepRunner:
    def __init__(self, steps: ResearchStepRepository, artifacts: ResearchArtifactRepository, events: ResearchEventStore | None = None) -> None:
        self.steps = steps
        self.artifacts = artifacts
        self.events = events

    def _emit(self, event: ResearchStepEvent) -> None:
        if self.events:
            self.events.append(event)

    async def execute(self, step: ResearchStepContract, executor: Callable[[ResearchStepContract], Awaitable[list[ResearchArtifactContract]]]) -> ResearchStepContract:
        self.steps.create(replace(step, status=ResearchStepStatus.RUNNING))
        self._emit(ResearchStepEvent.create(STEP_STARTED, step.run_id, step.step_id))
        try:
            for artifact in await executor(step):
                self.artifacts.add(artifact)
                self._emit(ResearchStepEvent.create(ARTIFACT_CREATED, step.run_id, step.step_id, {"artifact_type": artifact.artifact_type.value}))
            if not self.artifacts.all_verified(step.step_id, required_artifacts_for_step(step.step_type)):
                raise StepBlockedError()
            result = self.steps.update_status(step.step_id, ResearchStepStatus.COMPLETED)
            self._emit(ResearchStepEvent.create(STEP_COMPLETED, step.run_id, step.step_id))
            return result
        except StepBlockedError:
            result = self.steps.update_status(step.step_id, ResearchStepStatus.BLOCKED)
            self._emit(ResearchStepEvent.create(STEP_BLOCKED, step.run_id, step.step_id))
            return result
        except Exception as exc:
            result = self.steps.update_status(step.step_id, ResearchStepStatus.FAILED)
            self._emit(ResearchStepEvent.create(STEP_FAILED, step.run_id, step.step_id, {"error": str(exc)}))
            return result
