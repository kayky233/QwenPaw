"""Diagnosis and bounded repair policy for AutoResearch validation failures."""

from __future__ import annotations

from dataclasses import dataclass

from .failure_signature import FailureCategory, FailureSignature


@dataclass(frozen=True)
class Diagnosis:
    signature: FailureSignature
    root_cause: str
    proposed_action: str
    requires_human: bool


class DiagnosisBuilder:
    HUMAN_REQUIRED = {
        FailureCategory.SCOPE,
        FailureCategory.ENVIRONMENT,
    }

    def build(self, signature: FailureSignature) -> Diagnosis:
        requires_human = signature.category in self.HUMAN_REQUIRED
        actions = {
            FailureCategory.IMPLEMENTATION: "repair implementation and rerun focused validation",
            FailureCategory.TEST: "review test contract and expected behavior",
            FailureCategory.SCOPE: "request explicit scope expansion approval",
            FailureCategory.DEPENDENCY: "repair or declare dependency installation",
            FailureCategory.ENVIRONMENT: "request compatible execution environment",
            FailureCategory.FLAKY: "rerun once and compare failure fingerprint",
            FailureCategory.TIMEOUT: "reduce scope or increase approved timeout budget",
            FailureCategory.BUILD: "repair build configuration or compile errors",
            FailureCategory.LINT: "apply mechanical formatting and lint fixes",
            FailureCategory.TYPE: "repair type errors without weakening type checks",
            FailureCategory.UNKNOWN: "inspect evidence before any retry",
        }
        return Diagnosis(
            signature=signature,
            root_cause=signature.category.value,
            proposed_action=actions[signature.category],
            requires_human=requires_human,
        )


@dataclass(frozen=True)
class RepairBudget:
    max_repairs: int = 3
    max_scope_expansions: int = 1


class RepairPolicy:
    def __init__(self, budget: RepairBudget | None = None):
        self.budget = budget or RepairBudget()

    def may_repair(
        self,
        diagnosis: Diagnosis,
        repair_attempts: int,
        scope_expansions: int,
    ) -> bool:
        if diagnosis.requires_human:
            return False
        if diagnosis.signature.category == FailureCategory.SCOPE:
            return scope_expansions < self.budget.max_scope_expansions
        return repair_attempts < self.budget.max_repairs
