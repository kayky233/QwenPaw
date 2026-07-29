"""Build and verify staged feature execution plans."""

from __future__ import annotations

from dataclasses import dataclass

from .feature_dag import FeatureExecutionDAG, FeatureNode, FeatureNodeType
from .feature_spec import EndToEndScenario, FeatureSpec


@dataclass(frozen=True)
class EndToEndResult:
    scenario_id: str
    passed: bool
    evidence: str


@dataclass(frozen=True)
class FeatureAcceptanceResult:
    ready: bool
    blockers: tuple[str, ...]


class FeaturePlanBuilder:
    def build(self, spec: FeatureSpec) -> FeatureExecutionDAG:
        nodes = [
            FeatureNode(
                node_id="design",
                node_type=FeatureNodeType.DESIGN,
                objective=f"Design {spec.title}",
            ),
            FeatureNode(
                node_id="domain",
                node_type=FeatureNodeType.DOMAIN,
                objective="Define feature domain contracts",
                dependencies=("design",),
            ),
            FeatureNode(
                node_id="core",
                node_type=FeatureNodeType.CORE,
                objective="Implement core feature behavior",
                dependencies=("domain",),
            ),
            FeatureNode(
                node_id="integration",
                node_type=FeatureNodeType.INTEGRATION,
                objective="Integrate the feature with existing runtime paths",
                dependencies=("core",),
            ),
            FeatureNode(
                node_id="tests",
                node_type=FeatureNodeType.TESTS,
                objective="Verify acceptance criteria and invariants",
                dependencies=("integration",),
            ),
        ]
        if spec.migration_requirements:
            nodes.append(
                FeatureNode(
                    node_id="migration",
                    node_type=FeatureNodeType.MIGRATION,
                    objective="Implement and verify migration requirements",
                    dependencies=("core",),
                )
            )
        if spec.e2e_scenarios:
            dependencies = ["tests"]
            if spec.migration_requirements:
                dependencies.append("migration")
            nodes.append(
                FeatureNode(
                    node_id="e2e",
                    node_type=FeatureNodeType.E2E,
                    objective="Run end-to-end feature scenarios",
                    dependencies=tuple(dependencies),
                )
            )
        return FeatureExecutionDAG(tuple(nodes))


class FeatureAcceptanceGate:
    def evaluate(
        self,
        spec: FeatureSpec,
        *,
        verified_criteria: set[str],
        verified_invariants: set[str],
        e2e_results: tuple[EndToEndResult, ...],
    ) -> FeatureAcceptanceResult:
        blockers: list[str] = []
        for criterion in spec.acceptance_criteria:
            if criterion.criterion_id not in verified_criteria:
                blockers.append(f"criterion_not_verified:{criterion.criterion_id}")
        for invariant in spec.behavior_invariants:
            if invariant not in verified_invariants:
                blockers.append(f"invariant_not_verified:{invariant}")
        results = {item.scenario_id: item for item in e2e_results}
        for scenario in spec.e2e_scenarios:
            result = results.get(scenario.scenario_id)
            if result is None:
                blockers.append(f"e2e_missing:{scenario.scenario_id}")
            elif not result.passed:
                blockers.append(f"e2e_failed:{scenario.scenario_id}")
        return FeatureAcceptanceResult(not blockers, tuple(blockers))
