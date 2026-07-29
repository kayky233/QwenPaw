"""End-to-end Issue Campaign orchestration for AutoResearch."""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, replace
from enum import Enum
from typing import Protocol

from .candidate_checkpoint import CandidateCheckpoint
from .change_request_delivery import ChangeRequestResult
from .collaboration_contracts import ReviewDecision, ReviewVerdict
from .contracts import ResearchArtifactContract, ResearchArtifactType
from .episode_builder import IssueEpisodeBuilder
from .episode_evidence_gate import EpisodeEvidenceGate, EpisodeEvidenceResult
from .episode_package import EpisodeCommand, EpisodePackage
from .episode_validation import EpisodeValidationPlanner
from .execution_runner import ExecutionRunner
from .failure_signature import FailureSignature, build_failure_signature
from .issue_solver_service import IssueSolverService, PreparedIssueSolve
from .validation_pipeline_v2 import ValidationPipelineResult, ValidationPipelineV2

_SHA256_RE = re.compile(r"^[a-f0-9]{64}$")


class IssueCampaignStatus(str, Enum):
    DELIVERED = "delivered"
    NEEDS_REVISION = "needs_revision"
    BLOCKED = "blocked"
    FAILED = "failed"


@dataclass(frozen=True)
class IssueCampaignRequest:
    repository: str
    issue_number: int
    run_id: str
    base_revision: str
    task_type: str
    workspace: str
    acceptance_criteria: tuple[str, ...]
    commands: tuple[EpisodeCommand, ...]
    frozen_files: tuple[str, ...] = ()
    environment: dict[str, str] | None = None
    max_attempts: int = 3


@dataclass(frozen=True)
class CampaignCandidate:
    checkpoint: CandidateCheckpoint
    artifacts: tuple[ResearchArtifactContract, ...]


@dataclass(frozen=True)
class CampaignReview:
    decision: ReviewDecision
    report_artifact: ResearchArtifactContract


@dataclass(frozen=True)
class IssueCampaignAttempt:
    attempt: int
    candidate: CampaignCandidate
    validation: ValidationPipelineResult
    review: CampaignReview
    failure: FailureSignature | None = None


@dataclass(frozen=True)
class IssueCampaignOutcome:
    status: IssueCampaignStatus
    prepared: PreparedIssueSolve
    episode: EpisodePackage
    attempts: tuple[IssueCampaignAttempt, ...]
    artifacts: tuple[ResearchArtifactContract, ...]
    evidence: EpisodeEvidenceResult | None = None
    delivery: ChangeRequestResult | None = None
    reason: str = ""


class CampaignCandidateExecutor(Protocol):
    async def execute(
        self,
        episode: EpisodePackage,
        attempt: int,
        feedback: str,
    ) -> CampaignCandidate: ...


class CampaignReviewer(Protocol):
    async def review(
        self,
        episode: EpisodePackage,
        candidate: CampaignCandidate,
        artifacts: tuple[ResearchArtifactContract, ...],
    ) -> CampaignReview: ...


class CampaignDeliverer(Protocol):
    async def deliver(
        self,
        episode: EpisodePackage,
        candidate: CampaignCandidate,
        artifacts: tuple[ResearchArtifactContract, ...],
    ) -> ChangeRequestResult: ...


class IssueCampaignRunner:
    """Run a bounded issue-solving campaign with evidence-gated delivery."""

    def __init__(
        self,
        issue_service: IssueSolverService,
        execution_runner: ExecutionRunner,
        *,
        episode_builder: IssueEpisodeBuilder | None = None,
        validation_planner: EpisodeValidationPlanner | None = None,
        evidence_gate: EpisodeEvidenceGate | None = None,
    ) -> None:
        self.issue_service = issue_service
        self.execution_runner = execution_runner
        self.episode_builder = episode_builder or IssueEpisodeBuilder()
        self.validation_planner = validation_planner or EpisodeValidationPlanner()
        self.evidence_gate = evidence_gate or EpisodeEvidenceGate()

    async def run(
        self,
        request: IssueCampaignRequest,
        *,
        executor: CampaignCandidateExecutor,
        reviewer: CampaignReviewer,
        deliverer: CampaignDeliverer,
    ) -> IssueCampaignOutcome:
        if request.max_attempts <= 0:
            raise ValueError("max_attempts must be positive")

        prepared = self.issue_service.prepare(
            request.repository,
            request.issue_number,
        )
        episode = self.episode_builder.build(
            prepared,
            run_id=request.run_id,
            base_revision=request.base_revision,
            task_type=request.task_type,
            acceptance_criteria=request.acceptance_criteria,
            commands=request.commands,
            frozen_files=request.frozen_files,
            environment=request.environment,
        )
        validation_plan = self.validation_planner.compile(
            episode,
            workspace=request.workspace,
        )
        plan_artifact = self._plan_artifact(episode)
        attempts: list[IssueCampaignAttempt] = []
        feedback = ""
        latest_artifacts: tuple[ResearchArtifactContract, ...] = (plan_artifact,)
        accepted_parents = {episode.base_revision}

        for attempt_index in range(1, request.max_attempts + 1):
            try:
                candidate = await executor.execute(
                    episode,
                    attempt_index,
                    feedback,
                )
            except Exception as exc:
                return self._outcome(
                    IssueCampaignStatus.FAILED,
                    prepared,
                    episode,
                    attempts,
                    latest_artifacts,
                    reason=(
                        "candidate_execution_failed:"
                        f"{type(exc).__name__}:{exc}"
                    ),
                )

            invalid_candidate = self._validate_candidate(
                episode,
                candidate,
                accepted_parents,
            )
            if invalid_candidate:
                return self._outcome(
                    IssueCampaignStatus.BLOCKED,
                    prepared,
                    episode,
                    attempts,
                    (*latest_artifacts, *candidate.artifacts),
                    reason=invalid_candidate,
                )

            pipeline = ValidationPipelineV2(
                self.execution_runner,
                episode.run_id,
            )
            validation = await asyncio.to_thread(
                pipeline.execute,
                f"{episode.run_id}-test-{attempt_index}",
                validation_plan,
            )
            validation_artifacts = tuple(
                stage.artifact for stage in validation.stages
            )
            verified_checkpoint = replace(
                candidate.checkpoint,
                verification_passed=validation.succeeded,
            )
            candidate = replace(
                candidate,
                checkpoint=verified_checkpoint,
            )
            commit_artifact = self._commit_artifact(
                episode,
                candidate,
                verified=validation.succeeded,
            )
            pre_review_artifacts = (
                plan_artifact,
                *candidate.artifacts,
                *validation_artifacts,
                commit_artifact,
            )

            try:
                review = await reviewer.review(
                    episode,
                    candidate,
                    pre_review_artifacts,
                )
            except Exception as exc:
                return self._outcome(
                    IssueCampaignStatus.FAILED,
                    prepared,
                    episode,
                    attempts,
                    pre_review_artifacts,
                    reason=f"review_failed:{type(exc).__name__}:{exc}",
                )

            review_error = self._validate_review(episode, review)
            combined_artifacts = (
                *pre_review_artifacts,
                review.report_artifact,
            )
            if review_error:
                return self._outcome(
                    IssueCampaignStatus.BLOCKED,
                    prepared,
                    episode,
                    attempts,
                    combined_artifacts,
                    reason=review_error,
                )

            failure = self._failure_from_attempt(validation, review)
            attempts.append(
                IssueCampaignAttempt(
                    attempt=attempt_index,
                    candidate=candidate,
                    validation=validation,
                    review=review,
                    failure=failure,
                )
            )
            latest_artifacts = combined_artifacts

            if review.decision.verdict == ReviewVerdict.BLOCKED:
                return self._outcome(
                    IssueCampaignStatus.BLOCKED,
                    prepared,
                    episode,
                    attempts,
                    combined_artifacts,
                    reason="review_blocked",
                )

            if validation.succeeded and review.decision.approved:
                evidence = self.evidence_gate.evaluate(
                    episode,
                    combined_artifacts,
                )
                if not evidence.ready:
                    return self._outcome(
                        IssueCampaignStatus.BLOCKED,
                        prepared,
                        episode,
                        attempts,
                        combined_artifacts,
                        evidence=evidence,
                        reason="episode_evidence_gate_failed",
                    )
                try:
                    delivery = await deliverer.deliver(
                        episode,
                        candidate,
                        combined_artifacts,
                    )
                except Exception as exc:
                    return self._outcome(
                        IssueCampaignStatus.FAILED,
                        prepared,
                        episode,
                        attempts,
                        combined_artifacts,
                        evidence=evidence,
                        reason=f"delivery_failed:{type(exc).__name__}:{exc}",
                    )
                pr_artifact = self._delivery_artifact(
                    episode,
                    delivery,
                )
                return self._outcome(
                    IssueCampaignStatus.DELIVERED,
                    prepared,
                    episode,
                    attempts,
                    (*combined_artifacts, pr_artifact),
                    evidence=evidence,
                    delivery=delivery,
                    reason="verified_and_delivered",
                )

            feedback = self._feedback(validation, review, failure)
            accepted_parents.add(candidate.checkpoint.tree_revision)

        return self._outcome(
            IssueCampaignStatus.NEEDS_REVISION,
            prepared,
            episode,
            attempts,
            latest_artifacts,
            reason="repair_budget_exhausted",
        )

    @staticmethod
    def _outcome(
        status: IssueCampaignStatus,
        prepared: PreparedIssueSolve,
        episode: EpisodePackage,
        attempts: list[IssueCampaignAttempt],
        artifacts: tuple[ResearchArtifactContract, ...],
        *,
        evidence: EpisodeEvidenceResult | None = None,
        delivery: ChangeRequestResult | None = None,
        reason: str,
    ) -> IssueCampaignOutcome:
        return IssueCampaignOutcome(
            status=status,
            prepared=prepared,
            episode=episode,
            attempts=tuple(attempts),
            artifacts=artifacts,
            evidence=evidence,
            delivery=delivery,
            reason=reason,
        )

    @staticmethod
    def _plan_artifact(episode: EpisodePackage) -> ResearchArtifactContract:
        content = episode.to_json()
        return ResearchArtifactContract(
            artifact_id=f"{episode.episode_id}-plan",
            run_id=episode.run_id,
            step_id=f"{episode.run_id}-plan",
            artifact_type=ResearchArtifactType.PLAN,
            path="episode.json",
            content_hash=ResearchArtifactContract.hash_content(content),
            verified=True,
            metadata={"episode_digest": episode.digest()},
        )

    @staticmethod
    def _commit_artifact(
        episode: EpisodePackage,
        candidate: CampaignCandidate,
        *,
        verified: bool,
    ) -> ResearchArtifactContract:
        revision = candidate.checkpoint.tree_revision
        return ResearchArtifactContract(
            artifact_id=f"{candidate.checkpoint.candidate_id}-commit",
            run_id=episode.run_id,
            step_id=f"{episode.run_id}-delivery",
            artifact_type=ResearchArtifactType.COMMIT,
            path=f"git://{revision}",
            content_hash=ResearchArtifactContract.hash_content(revision),
            verified=verified and bool(revision),
            metadata={
                "candidate_id": candidate.checkpoint.candidate_id,
                "parent_revision": candidate.checkpoint.parent_revision,
                "tree_revision": revision,
                "risk_score": candidate.checkpoint.risk_score,
            },
        )

    @staticmethod
    def _delivery_artifact(
        episode: EpisodePackage,
        delivery: ChangeRequestResult,
    ) -> ResearchArtifactContract:
        return ResearchArtifactContract(
            artifact_id=f"{episode.episode_id}-pull-request",
            run_id=episode.run_id,
            step_id=f"{episode.run_id}-delivery",
            artifact_type=ResearchArtifactType.PULL_REQUEST,
            path=delivery.url,
            content_hash=ResearchArtifactContract.hash_content(delivery.url),
            verified=bool(delivery.url),
            metadata={"number": delivery.number},
        )

    @staticmethod
    def _validate_candidate(
        episode: EpisodePackage,
        candidate: CampaignCandidate,
        accepted_parents: set[str],
    ) -> str:
        checkpoint = candidate.checkpoint
        if checkpoint.run_id != episode.run_id:
            return "candidate_run_id_mismatch"
        if checkpoint.parent_revision not in accepted_parents:
            return "candidate_parent_revision_mismatch"
        if not checkpoint.candidate_id.strip():
            return "candidate_id_missing"
        if not checkpoint.tree_revision.strip():
            return "candidate_tree_revision_missing"
        if not _SHA256_RE.fullmatch(checkpoint.diff_hash):
            return "candidate_diff_hash_invalid"
        if not candidate.artifacts:
            return "candidate_artifacts_missing"
        has_diff = False
        for artifact in candidate.artifacts:
            if artifact.run_id != episode.run_id:
                return f"candidate_artifact_run_mismatch:{artifact.artifact_id}"
            if artifact.artifact_type == ResearchArtifactType.CODE_DIFF:
                has_diff = True
        if not has_diff:
            return "candidate_code_diff_missing"
        return ""

    @staticmethod
    def _validate_review(
        episode: EpisodePackage,
        review: CampaignReview,
    ) -> str:
        if review.decision.run_id != episode.run_id:
            return "review_run_id_mismatch"
        artifact = review.report_artifact
        if artifact.run_id != episode.run_id:
            return "review_artifact_run_id_mismatch"
        if artifact.artifact_type != ResearchArtifactType.REPORT:
            return "review_report_artifact_type_invalid"
        if not artifact.verified:
            return "review_report_not_verified"
        return ""

    @staticmethod
    def _failure_from_attempt(
        validation: ValidationPipelineResult,
        review: CampaignReview,
    ) -> FailureSignature | None:
        for stage in validation.stages:
            if stage.succeeded:
                continue
            metadata = stage.artifact.metadata
            return build_failure_signature(
                str(metadata.get("stderr", "")),
                str(metadata.get("stdout", "")),
                bool(metadata.get("timed_out", False)),
            )
        if review.decision.verdict == ReviewVerdict.REQUEST_CHANGES:
            message = "\n".join(
                (*review.decision.findings, *review.decision.required_changes)
            )
            return build_failure_signature(
                f"review requested changes: {message}"
            )
        return None

    @staticmethod
    def _feedback(
        validation: ValidationPipelineResult,
        review: CampaignReview,
        failure: FailureSignature | None,
    ) -> str:
        parts: list[str] = []
        if failure is not None:
            parts.append(
                "Failure signature: "
                f"{failure.category.value}/{failure.fingerprint}"
            )
            if failure.message:
                parts.append(failure.message)
        failed_stages = [
            stage.stage.value
            for stage in validation.stages
            if not stage.succeeded
        ]
        if failed_stages:
            parts.append("Failed validation stages: " + ", ".join(failed_stages))
        if review.decision.findings:
            parts.append("Reviewer findings: " + "; ".join(review.decision.findings))
        if review.decision.required_changes:
            parts.append(
                "Required changes: "
                + "; ".join(review.decision.required_changes)
            )
        return "\n".join(parts).strip()
