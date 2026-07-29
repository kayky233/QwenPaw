"""Contracts for evidence-driven multi-agent AutoResearch collaboration."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from .contracts import ResearchArtifactType


class ResearchAgentRole(str, Enum):
    PLANNER = "planner"
    IMPLEMENTER = "implementer"
    TESTER = "tester"
    REVIEWER = "reviewer"


class ReviewVerdict(str, Enum):
    APPROVE = "approve"
    REQUEST_CHANGES = "request_changes"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class TaskEnvelope:
    task_id: str
    run_id: str
    step_id: str
    role: ResearchAgentRole
    objective: str
    input_artifact_ids: tuple[str, ...] = ()
    expected_artifact_types: tuple[ResearchArtifactType, ...] = ()
    allowed_paths: tuple[str, ...] = ()
    deadline: datetime | None = None
    read_only: bool = False

    def __post_init__(self) -> None:
        if self.role == ResearchAgentRole.REVIEWER and not self.read_only:
            raise ValueError("reviewer task envelopes must be read-only")
        if self.read_only and self.expected_artifact_types and any(
            item in {
                ResearchArtifactType.CODE_DIFF,
                ResearchArtifactType.COMMIT,
                ResearchArtifactType.PULL_REQUEST,
            }
            for item in self.expected_artifact_types
        ):
            raise ValueError("read-only agents cannot produce mutation artifacts")


@dataclass(frozen=True)
class ArtifactHandoff:
    task_id: str
    run_id: str
    step_id: str
    producer_role: ResearchAgentRole
    artifact_ids: tuple[str, ...]
    summary: str
    completed_at: datetime


@dataclass(frozen=True)
class ReviewDecision:
    run_id: str
    step_id: str
    verdict: ReviewVerdict
    findings: tuple[str, ...] = ()
    required_changes: tuple[str, ...] = ()

    @property
    def approved(self) -> bool:
        return self.verdict == ReviewVerdict.APPROVE
