"""Safety gate for autonomous changes to the AutoResearch harness itself."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from .contracts import ResearchArtifactContract, ResearchArtifactType
from .episode_package import EpisodePackage

_SHA256_RE = re.compile(r"^[a-f0-9]{64}$")


class HarnessGateDisposition(str, Enum):
    APPROVED = "approved"
    BLOCKED = "blocked"
    HUMAN_REVIEW = "human_review"


@dataclass(frozen=True)
class HarnessMetric:
    name: str
    baseline: float
    candidate: float
    lower_is_better: bool
    allowed_regression_ratio: float = 0.0

    def validate(self) -> None:
        if not self.name.strip():
            raise ValueError("metric name is required")
        if self.allowed_regression_ratio < 0:
            raise ValueError("allowed_regression_ratio cannot be negative")

    def regressed(self) -> bool:
        self.validate()
        tolerance = abs(self.baseline) * self.allowed_regression_ratio
        if self.lower_is_better:
            return self.candidate > self.baseline + tolerance
        return self.candidate < self.baseline - tolerance


@dataclass(frozen=True)
class HarnessChangeProposal:
    proposal_id: str
    episode_digest: str
    proposer_agent: str
    reviewer_agent: str
    rollback_revision: str
    changed_paths: tuple[str, ...]
    artifacts: tuple[ResearchArtifactContract, ...]
    metrics: tuple[HarnessMetric, ...]


@dataclass(frozen=True)
class HarnessGateResult:
    disposition: HarnessGateDisposition
    reasons: tuple[str, ...]

    @property
    def approved(self) -> bool:
        return self.disposition == HarnessGateDisposition.APPROVED


class HarnessEvolutionGate:
    """Require reproducibility, independent review, evidence, and no regression."""

    DEFAULT_HUMAN_REVIEW_PATHS = frozenset(
        {
            "src/qwenpaw/app/routers/research_scope.py",
            "src/qwenpaw/app/routers/research_state_machine.py",
            "src/qwenpaw/app/routers/research_delivery_lifecycle_service.py",
            "src/qwenpaw/research_ledger/harness_evolution.py",
        }
    )

    def __init__(
        self,
        human_review_paths: frozenset[str] | None = None,
    ) -> None:
        self.human_review_paths = (
            human_review_paths
            if human_review_paths is not None
            else self.DEFAULT_HUMAN_REVIEW_PATHS
        )

    def evaluate(
        self,
        episode: EpisodePackage,
        proposal: HarnessChangeProposal,
    ) -> HarnessGateResult:
        episode.validate()
        blocking: list[str] = []
        human_review: list[str] = []

        if not proposal.proposal_id.strip():
            blocking.append("proposal_id_missing")
        if not _SHA256_RE.fullmatch(proposal.episode_digest):
            blocking.append("episode_digest_invalid")
        elif proposal.episode_digest != episode.digest():
            blocking.append("episode_digest_mismatch")
        if not proposal.proposer_agent.strip():
            blocking.append("proposer_agent_missing")
        if not proposal.reviewer_agent.strip():
            blocking.append("reviewer_agent_missing")
        if proposal.proposer_agent == proposal.reviewer_agent:
            blocking.append("independent_reviewer_required")
        if not proposal.rollback_revision.strip():
            blocking.append("rollback_revision_missing")
        if not proposal.changed_paths:
            blocking.append("changed_paths_missing")

        approved_scope = set(episode.modifiable_files)
        changed_paths = set(proposal.changed_paths)
        if len(changed_paths) != len(proposal.changed_paths):
            blocking.append("duplicate_changed_path")
        out_of_scope = sorted(changed_paths - approved_scope)
        if out_of_scope:
            blocking.append("scope_violation:" + ",".join(out_of_scope))

        artifact_by_type: dict[
            ResearchArtifactType,
            list[ResearchArtifactContract],
        ] = {}
        for artifact in proposal.artifacts:
            if artifact.run_id != episode.run_id:
                blocking.append(
                    f"artifact_run_mismatch:{artifact.artifact_id}"
                )
            artifact_by_type.setdefault(artifact.artifact_type, []).append(artifact)

        required_types = {
            item.artifact_type
            for item in episode.expected_artifacts
            if item.required
        }
        required_types.update(
            {
                ResearchArtifactType.TEST_RESULT,
                ResearchArtifactType.REPORT,
            }
        )
        for artifact_type in sorted(required_types, key=lambda item: item.value):
            candidates = artifact_by_type.get(artifact_type, ())
            if not any(item.verified for item in candidates):
                blocking.append(
                    f"verified_artifact_missing:{artifact_type.value}"
                )

        if not proposal.metrics:
            blocking.append("metric_evidence_missing")
        for metric in proposal.metrics:
            try:
                regressed = metric.regressed()
            except ValueError as exc:
                blocking.append(f"metric_invalid:{exc}")
                continue
            if regressed:
                blocking.append(f"metric_regression:{metric.name}")

        protected = sorted(changed_paths & self.human_review_paths)
        if protected:
            human_review.append(
                "protected_harness_paths:" + ",".join(protected)
            )

        if blocking:
            return HarnessGateResult(
                HarnessGateDisposition.BLOCKED,
                tuple(dict.fromkeys(blocking)),
            )
        if human_review:
            return HarnessGateResult(
                HarnessGateDisposition.HUMAN_REVIEW,
                tuple(human_review),
            )
        return HarnessGateResult(
            HarnessGateDisposition.APPROVED,
            ("evidence_and_regression_gates_passed",),
        )
