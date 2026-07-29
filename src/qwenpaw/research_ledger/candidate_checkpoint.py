"""Candidate checkpoints for bounded AutoResearch repair loops."""

from __future__ import annotations

from dataclasses import dataclass

from .failure_signature import FailureSignature


@dataclass(frozen=True)
class CandidateCheckpoint:
    candidate_id: str
    run_id: str
    parent_revision: str
    tree_revision: str
    diff_hash: str
    diagnosis: FailureSignature | None = None
    verification_passed: bool = False
    risk_score: float = 0.0


class CandidateSelector:
    def prefer(
        self,
        current: CandidateCheckpoint | None,
        candidate: CandidateCheckpoint,
    ) -> CandidateCheckpoint:
        if current is None:
            return candidate
        if candidate.verification_passed and not current.verification_passed:
            return candidate
        if candidate.verification_passed == current.verification_passed:
            if candidate.risk_score < current.risk_score:
                return candidate
        return current
