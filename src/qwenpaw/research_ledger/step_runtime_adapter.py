"""Adapter that maps legacy research phases to durable step contracts."""

from __future__ import annotations

from dataclasses import dataclass

from .contracts import ResearchStepType


@dataclass(frozen=True)
class RuntimeStepMapping:
    legacy_phase: str
    step_type: ResearchStepType


DEFAULT_RUNTIME_STEPS = (
    RuntimeStepMapping("DISCOVERY", ResearchStepType.DISCOVERY),
    RuntimeStepMapping("PLAN", ResearchStepType.PLAN),
    RuntimeStepMapping("PROGRAM", ResearchStepType.IMPLEMENT),
    RuntimeStepMapping("JUDGE", ResearchStepType.TEST),
    RuntimeStepMapping("BENCHMARK", ResearchStepType.BENCHMARK),
    RuntimeStepMapping("REVIEW", ResearchStepType.REVIEW),
    RuntimeStepMapping("DELIVERY", ResearchStepType.DELIVERY),
)


def resolve_step_type(phase: str) -> ResearchStepType:
    normalized = phase.strip().upper()
    for item in DEFAULT_RUNTIME_STEPS:
        if item.legacy_phase == normalized:
            return item.step_type
    raise ValueError(f"unsupported research phase: {phase}")


def build_runtime_steps() -> tuple[RuntimeStepMapping, ...]:
    return DEFAULT_RUNTIME_STEPS
