"""Feature-mode contracts for multi-stage AutoResearch delivery."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AcceptanceCriterion:
    criterion_id: str
    description: str
    verification: str


@dataclass(frozen=True)
class EndToEndScenario:
    scenario_id: str
    title: str
    preconditions: tuple[str, ...]
    actions: tuple[str, ...]
    expected_outcomes: tuple[str, ...]


@dataclass(frozen=True)
class FeatureSpec:
    feature_id: str
    title: str
    objective: str
    acceptance_criteria: tuple[AcceptanceCriterion, ...]
    behavior_invariants: tuple[str, ...] = ()
    migration_requirements: tuple[str, ...] = ()
    rollout_requirements: tuple[str, ...] = ()
    e2e_scenarios: tuple[EndToEndScenario, ...] = ()

    def __post_init__(self) -> None:
        if not self.acceptance_criteria:
            raise ValueError("feature requires at least one acceptance criterion")
        criterion_ids = [item.criterion_id for item in self.acceptance_criteria]
        if len(criterion_ids) != len(set(criterion_ids)):
            raise ValueError("feature acceptance criterion ids must be unique")
        scenario_ids = [item.scenario_id for item in self.e2e_scenarios]
        if len(scenario_ids) != len(set(scenario_ids)):
            raise ValueError("feature e2e scenario ids must be unique")
