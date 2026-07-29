"""Contracts for durable AutoResearch execution steps and artifacts.

These are intentionally framework-independent. Runtime services can persist
these objects in the Research Ledger without coupling orchestration code to ORM
models.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from enum import Enum
from hashlib import sha256
import json
from typing import Any


class ResearchStepType(str, Enum):
    DISCOVERY = "discovery"
    PLAN = "plan"
    IMPLEMENT = "implement"
    TEST = "test"
    BENCHMARK = "benchmark"
    REVIEW = "review"
    DELIVERY = "delivery"


class ResearchStepStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"


class ResearchArtifactType(str, Enum):
    PLAN = "plan"
    PROGRAM = "program"
    CODE_DIFF = "code_diff"
    TEST_RESULT = "test_result"
    BENCHMARK_RESULT = "benchmark_result"
    LOG = "log"
    REPORT = "report"
    COMMIT = "commit"
    PULL_REQUEST = "pull_request"


@dataclass(frozen=True)
class ResearchStepContract:
    """A durable unit of work inside a research run."""

    step_id: str
    run_id: str
    step_type: ResearchStepType
    index: int
    executor: str
    required_artifacts: tuple[ResearchArtifactType, ...] = ()
    input_artifacts: tuple[str, ...] = ()
    validation_rules: tuple[str, ...] = ()
    status: ResearchStepStatus = ResearchStepStatus.PENDING


@dataclass(frozen=True)
class ResearchArtifactContract:
    """Evidence produced by a step."""

    artifact_id: str
    run_id: str
    step_id: str
    artifact_type: ResearchArtifactType
    path: str
    content_hash: str
    verified: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    @staticmethod
    def hash_content(content: str) -> str:
        return sha256(content.encode("utf-8")).hexdigest()

    def digest(self) -> str:
        payload = json.dumps(asdict(self), sort_keys=True, default=str)
        return sha256(payload.encode("utf-8")).hexdigest()


STEP_REQUIRED_ARTIFACTS: dict[ResearchStepType, tuple[ResearchArtifactType, ...]] = {
    ResearchStepType.PLAN: (ResearchArtifactType.PLAN,),
    ResearchStepType.IMPLEMENT: (ResearchArtifactType.CODE_DIFF,),
    ResearchStepType.TEST: (ResearchArtifactType.TEST_RESULT,),
    ResearchStepType.BENCHMARK: (ResearchArtifactType.BENCHMARK_RESULT,),
    ResearchStepType.REVIEW: (ResearchArtifactType.REPORT,),
    ResearchStepType.DELIVERY: (
        ResearchArtifactType.COMMIT,
        ResearchArtifactType.PULL_REQUEST,
    ),
}


def required_artifacts_for_step(step_type: ResearchStepType) -> tuple[ResearchArtifactType, ...]:
    return STEP_REQUIRED_ARTIFACTS.get(step_type, ())
