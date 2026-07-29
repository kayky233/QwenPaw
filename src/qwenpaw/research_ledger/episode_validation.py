"""Compile portable Episode commands into executable validation plans."""

from __future__ import annotations

from pathlib import Path

from .episode_package import EpisodePackage
from .validation_contract import (
    ValidationCommand,
    ValidationPlan,
    ValidationStage,
)

_STAGE_ALIASES = {
    "unit": ValidationStage.FOCUSED,
    "focused": ValidationStage.FOCUSED,
    "affected": ValidationStage.AFFECTED,
    "integration": ValidationStage.AFFECTED,
    "quality": ValidationStage.QUALITY,
    "lint": ValidationStage.QUALITY,
    "type": ValidationStage.QUALITY,
    "build": ValidationStage.BUILD,
    "e2e": ValidationStage.E2E,
}


class EpisodeValidationPlanner:
    def compile(
        self,
        episode: EpisodePackage,
        *,
        workspace: str,
    ) -> ValidationPlan:
        episode.validate()
        root = Path(workspace).resolve()
        commands: list[ValidationCommand] = []
        for command in episode.commands:
            stage_key = command.stage.strip().casefold()
            try:
                stage = _STAGE_ALIASES[stage_key]
            except KeyError as exc:
                raise ValueError(
                    f"unsupported episode command stage: {command.stage!r}"
                ) from exc
            cwd = (root / command.cwd).resolve()
            try:
                cwd.relative_to(root)
            except ValueError as exc:
                raise ValueError(
                    f"episode command cwd escapes workspace: {command.cwd!r}"
                ) from exc
            commands.append(
                ValidationCommand(
                    stage=stage,
                    argv=command.argv,
                    cwd=str(cwd),
                    required=command.required,
                    timeout_seconds=command.timeout_seconds,
                )
            )
        return ValidationPlan(tuple(commands))
