import pytest

from qwenpaw.research_ledger.feature_dag import (
    FeatureExecutionDAG,
    FeatureNode,
    FeatureNodeType,
)
from qwenpaw.research_ledger.feature_runtime import (
    EndToEndResult,
    FeatureAcceptanceGate,
    FeaturePlanBuilder,
)
from qwenpaw.research_ledger.feature_spec import (
    AcceptanceCriterion,
    EndToEndScenario,
    FeatureSpec,
)


def _feature():
    return FeatureSpec(
        feature_id="memory-ttl",
        title="Memory TTL",
        objective="Expire stale memories automatically",
        acceptance_criteria=(
            AcceptanceCriterion(
                "ttl-expiry",
                "Expired memories are not returned",
                "integration test",
            ),
        ),
        behavior_invariants=("non-expired memories remain readable",),
        migration_requirements=("existing records without ttl remain valid",),
        e2e_scenarios=(
            EndToEndScenario(
                "expire-memory",
                "Expire a stored memory",
                ("memory service running",),
                ("store memory with ttl", "wait for expiry", "query memory"),
                ("expired memory is absent",),
            ),
        ),
    )


def test_feature_plan_contains_migration_and_e2e():
    order = FeaturePlanBuilder().build(_feature()).topological_order()
    assert [item.node_id for item in order][-2:] == ["migration", "e2e"]


def test_feature_acceptance_gate_requires_all_evidence():
    spec = _feature()
    result = FeatureAcceptanceGate().evaluate(
        spec,
        verified_criteria={"ttl-expiry"},
        verified_invariants={"non-expired memories remain readable"},
        e2e_results=(EndToEndResult("expire-memory", True, "passed"),),
    )
    assert result.ready


def test_feature_dag_rejects_cycles():
    with pytest.raises(ValueError, match="cycle"):
        FeatureExecutionDAG(
            (
                FeatureNode("a", FeatureNodeType.CORE, "a", ("b",)),
                FeatureNode("b", FeatureNodeType.TESTS, "b", ("a",)),
            )
        )
