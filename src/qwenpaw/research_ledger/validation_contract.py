"""Validation plan contracts for focused and affected test execution."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ValidationStage(str, Enum):
    FOCUSED = "focused"
    AFFECTED = "affected"
    QUALITY = "quality"
    BUILD = "build"
    E2E = "e2e"


@dataclass(frozen=True)
class ValidationCommand:
    stage: ValidationStage
    argv: tuple[str, ...]
    cwd: str
    required: bool = True
    timeout_seconds: float = 300.0


@dataclass(frozen=True)
class ValidationPlan:
    commands: tuple[ValidationCommand, ...]

    def required_commands(self) -> tuple[ValidationCommand, ...]:
        return tuple(item for item in self.commands if item.required)
