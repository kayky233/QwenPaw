"""End-to-end Issue Campaign orchestration for AutoResearch."""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, replace
from enum import Enum
from typing import Protocol

from .campaign_delivery_contract import CampaignDeliveryReceipt
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
    modifiable_files: tuple[str, ...] = ()


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
    delivery_receipt: CampaignDeliveryReceipt | None = None
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
    ) -> CampaignDeliveryReceipt: ...


class IssueCampaignRunner:
    """Run a bounded issue-solving campaign with two evidence gates."""

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
            modifiable_files=request.modifiable_files,
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
            candidate = replace(
                candidate,
                checkpoint=replace(
                    candidate.checkpoint,
                    verification_passed=validation.succeeded,
                ),
            )
            pre_review_artifacts = (
                plan_artifact,
                *candidate.artifacts,
                *validation_artifacts,
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
                pre_evidence = self.evidence_gate.evaluate(
                    episode,
                    combined_artifacts,
                    required_types=self._pre_delivery_types(episode),
                )
                if not pre_evidence.ready:
                    return self._outcome(
                        IssueCampaignStatus.BLOCKED,
                        prepared,
                        episode,
                        attempts,
                        combined_artifacts,
                        evidence=pre_evidence,
                        reason="pre_delivery_evidence_gate_failed",
                    )

                try:
                    receipt = await deliverer.deliver(
                        episode,
                        candidate,
                        combined_artifacts,
                    )
                    receipt.validate(episode.run_id)
                except Exception as exc:
                    return self._outcome(
                        IssueCampaignStatus.FAILED,
                        prepared,
                        episode,
                        attempts,
                        combined_artifacts,
                        evidence=pre_evidence,
                        reason=f"delivery_failed:{type(exc).__name__}:{exc}",
                    )

                final_artifacts = self._merge_artifacts(
                    combined_artifacts,
                    receipt.artifacts,
                )
                post_evidence = self.evidence_gate.evaluate(
                    episode,
                    final_artifacts,
                    required_types=self._post_delivery_types(episode),
                )
                if not post_evidence.ready:
                    return self._outcome(
                        IssueCampaignStatus.BLOCKED,
                        prepared,
                        episode,
                        attempts,
                        final_artifacts,
                        evidence=post_evidence,
                        delivery=receipt.change_request,
                        delivery_receipt=receipt,
                        reason="post_delivery_evidence_gate_failed",
                    )

                return self._outcome(
                    IssueCampaignStatus.DELIVERED,
                    prepared,
                    episode,
                    attempts,
                    final_artifacts,
                    evidence=post_evidence,
                    delivery=receipt.change_request,
                    delivery_receipt=receipt,
                    reason="verified_committed_and_delivered",
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
    def _pre_delivery_types(
        episode: EpisodePackage,
    ) -> set[ResearchArtifactType]:
        required = {
            item.artifact_type
            for item in episode.expected_artifacts
            if item.required
        }
        return required - {
            ResearchArtifactType.COMMIT,
            ResearchArtifactType.PULL_REQUEST,
        }

    @staticmethod
    def _post_delivery_types(
        episode: EpisodePackage,
    ) -> set[ResearchArtifactType]:
        required = {
            item.artifact_type
            for item in episode.expected_artifacts
            if item.required
        }
        return required | {ResearchArtifactType.PULL_REQUEST}

    @staticmethod
    def _merge_artifacts(
        current: tuple[ResearchArtifactContract, ...],
        delivered: tuple[ResearchArtifactContract, ...],
    ) -> tuple[ResearchArtifactContract, ...]:
        merged: dict[str, ResearchArtifactContract] = {
            artifact.artifact_id: artifact for artifact in current
        }
        for artifact in delivered:
            merged[artifact.artifact_id] = artifact
        return tuple(merged.values())

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
        delivery_receipt: CampaignDeliveryReceipt | None = None,
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
            delivery_receipt=delivery_receipt,
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

        diff_artifacts = tuple(
            artifact
            for artifact in candidate.artifacts
            if artifact.artifact_type == ResearchArtifactType.CODE_DIFF
        )
        if not diff_artifacts:
            return "candidate_code_diff_missing"
        for artifact in candidate.artifacts:
            if artifact.run_id != episode.run_id:
                return f"candidate_artifact_run_mismatch:{artifact.artifact_id}"
        for artifact in diff_artifacts:
            if not artifact.verified:
                return f"candidate_code_diff_not_verified:{artifact.artifact_id}"
            if artifact.content_hash != checkpoint.diff_hash:
                return f"candidate_diff_hash_mismatch:{artifact.artifact_id}"
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
