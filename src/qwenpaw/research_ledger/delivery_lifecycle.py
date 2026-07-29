"""CI, review, and merge-readiness contracts for delivered changes."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .contracts import ResearchArtifactType
from .step_repository import ResearchArtifactRepository


class DeliveryStatus(str, Enum):
    DRAFT_PR = "draft_pr"
    CI_WAITING = "ci_waiting"
    CI_FAILED = "ci_failed"
    REVIEW_WAITING = "review_waiting"
    CHANGES_REQUESTED = "changes_requested"
    MERGE_READY = "merge_ready"


class CICheckStatus(str, Enum):
    PENDING = "pending"
    PASSED = "passed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class CICheck:
    name: str
    status: CICheckStatus
    url: str = ""
    summary: str = ""


@dataclass(frozen=True)
class CIReport:
    commit_sha: str
    checks: tuple[CICheck, ...]
    attempt: int = 1

    @property
    def status(self) -> CICheckStatus:
        if any(item.status == CICheckStatus.FAILED for item in self.checks):
            return CICheckStatus.FAILED
        if any(item.status == CICheckStatus.CANCELLED for item in self.checks):
            return CICheckStatus.CANCELLED
        if self.checks and all(
            item.status == CICheckStatus.PASSED for item in self.checks
        ):
            return CICheckStatus.PASSED
        return CICheckStatus.PENDING


@dataclass(frozen=True)
class ReviewThread:
    thread_id: str
    author: str
    body: str
    resolved: bool = False


@dataclass(frozen=True)
class MergeReadiness:
    ready: bool
    status: DeliveryStatus
    blockers: tuple[str, ...] = ()


class MergeReadinessEvaluator:
    REQUIRED_ARTIFACTS = (
        ResearchArtifactType.CODE_DIFF,
        ResearchArtifactType.TEST_RESULT,
        ResearchArtifactType.REPORT,
        ResearchArtifactType.COMMIT,
        ResearchArtifactType.PULL_REQUEST,
    )

    def __init__(self, artifacts: ResearchArtifactRepository) -> None:
        self.artifacts = artifacts

    def evaluate(
        self,
        *,
        artifact_step_ids: dict[ResearchArtifactType, str],
        ci_report: CIReport | None,
        review_threads: tuple[ReviewThread, ...] = (),
    ) -> MergeReadiness:
        blockers: list[str] = []
        for artifact_type in self.REQUIRED_ARTIFACTS:
            step_id = artifact_step_ids.get(artifact_type)
            if not step_id or not self.artifacts.has_verified_type(
                step_id,
                artifact_type,
            ):
                blockers.append(f"missing_verified_artifact:{artifact_type.value}")

        if ci_report is None or ci_report.status == CICheckStatus.PENDING:
            blockers.append("ci_pending")
        elif ci_report.status != CICheckStatus.PASSED:
            blockers.append(f"ci_{ci_report.status.value}")

        unresolved = tuple(item for item in review_threads if not item.resolved)
        if unresolved:
            blockers.append(f"unresolved_review_threads:{len(unresolved)}")

        if not blockers:
            return MergeReadiness(True, DeliveryStatus.MERGE_READY)
        if any(item.startswith("ci_failed") for item in blockers):
            status = DeliveryStatus.CI_FAILED
        elif unresolved:
            status = DeliveryStatus.CHANGES_REQUESTED
        elif "ci_pending" in blockers:
            status = DeliveryStatus.CI_WAITING
        else:
            status = DeliveryStatus.REVIEW_WAITING
        return MergeReadiness(False, status, tuple(blockers))
