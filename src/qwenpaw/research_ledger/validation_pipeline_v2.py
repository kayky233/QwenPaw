"""Execute validation plans and collect deterministic evidence."""

from __future__ import annotations

from dataclasses import dataclass

from .contracts import ResearchArtifactContract, ResearchArtifactType
from .execution_evidence import ExecutionEvidenceCollector
from .execution_runner import CommandRequest, ExecutionRunner
from .validation_contract import ValidationPlan, ValidationStage


@dataclass(frozen=True)
class ValidationStageResult:
    stage: ValidationStage
    succeeded: bool
    artifact: ResearchArtifactContract


@dataclass(frozen=True)
class ValidationPipelineResult:
    stages: tuple[ValidationStageResult, ...]

    @property
    def succeeded(self) -> bool:
        return all(stage.succeeded for stage in self.stages)


class ValidationPipelineV2:
    def __init__(self, runner: ExecutionRunner, run_id: str):
        self.runner = runner
        self.collector = ExecutionEvidenceCollector(run_id)

    def execute(self, step_id: str, plan: ValidationPlan) -> ValidationPipelineResult:
        results: list[ValidationStageResult] = []
        for index, command in enumerate(plan.commands):
            command_result = self.runner.run(
                CommandRequest(
                    argv=command.argv,
                    cwd=command.cwd,
                    timeout_seconds=command.timeout_seconds,
                )
            )
            artifact = self.collector.collect(
                step_id=step_id,
                artifact_id=f"{step_id}-{command.stage.value}-{index}",
                artifact_type=ResearchArtifactType.TEST_RESULT,
                result=command_result,
            )
            results.append(
                ValidationStageResult(
                    stage=command.stage,
                    succeeded=command_result.succeeded or not command.required,
                    artifact=artifact,
                )
            )
            if command.required and not command_result.succeeded:
                break
        return ValidationPipelineResult(tuple(results))
