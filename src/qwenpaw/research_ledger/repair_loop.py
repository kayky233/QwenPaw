"""Bounded repair loop driven by failure signatures and candidate checkpoints."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable

from .candidate_checkpoint import CandidateCheckpoint, CandidateSelector
from .diagnosis import DiagnosisBuilder, RepairPolicy
from .failure_signature import FailureSignature


@dataclass(frozen=True)
class RepairLoopResult:
    best_candidate: CandidateCheckpoint | None
    attempts: int
    stopped_reason: str


class RepairLoopController:
    def __init__(
        self,
        policy: RepairPolicy | None = None,
        selector: CandidateSelector | None = None,
        diagnosis_builder: DiagnosisBuilder | None = None,
    ) -> None:
        self.policy = policy or RepairPolicy()
        self.selector = selector or CandidateSelector()
        self.diagnosis_builder = diagnosis_builder or DiagnosisBuilder()

    async def run(
        self,
        initial_failure: FailureSignature,
        repair: Callable[[int, FailureSignature], Awaitable[CandidateCheckpoint]],
        next_failure: Callable[[CandidateCheckpoint], Awaitable[FailureSignature | None]],
    ) -> RepairLoopResult:
        best: CandidateCheckpoint | None = None
        failure = initial_failure
        attempts = 0
        while True:
            diagnosis = self.diagnosis_builder.build(failure)
            if not self.policy.may_repair(diagnosis, attempts, 0):
                return RepairLoopResult(best, attempts, "repair_budget_or_human_gate")
            candidate = await repair(attempts, failure)
            attempts += 1
            best = self.selector.prefer(best, candidate)
            if candidate.verification_passed:
                return RepairLoopResult(best, attempts, "verified_candidate")
            failure = await next_failure(candidate)
            if failure is None:
                return RepairLoopResult(best, attempts, "missing_failure_evidence")
