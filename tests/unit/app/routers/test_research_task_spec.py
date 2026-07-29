from types import SimpleNamespace

import pytest

from qwenpaw.app.routers.research_task_spec import (
    BaselineExpectation,
    DeliveryMode,
    ResearchTaskType,
    TaskSpec,
    build_task_spec,
    delivery_contract_for,
    infer_task_type,
    resolve_task_spec,
    task_contract_instructions,
    task_spec_basis,
    validation_contract_for,
)


def _dialog(**overrides):
    values = {
        "plan_id": "plan-task-spec",
        "goal": "Fix issue #42",
        "plan_markdown": "# Plan\n\nTask Type: bug_fix\n",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


@pytest.mark.parametrize(
    ("declared", "expected"),
    [
        ("bug_fix", ResearchTaskType.BUG_FIX),
        ("feature", ResearchTaskType.FEATURE),
        ("refactor", ResearchTaskType.REFACTOR),
        ("performance", ResearchTaskType.PERFORMANCE),
        ("research", ResearchTaskType.RESEARCH),
        ("bug fix", ResearchTaskType.BUG_FIX),
        ("feature-development", ResearchTaskType.FEATURE),
    ],
)
def test_explicit_task_type_declaration_wins(
    declared: str,
    expected: ResearchTaskType,
) -> None:
    task_type, source, confidence, _ = infer_task_type(
        "Fix a bug",
        f"# Plan\n\nTask Type: {declared}\n",
    )

    assert task_type is expected
    assert source == "explicit_plan"
    assert confidence == 1.0


@pytest.mark.parametrize(
    ("goal", "expected"),
    [
        ("Fix a crash in the stream parser", ResearchTaskType.BUG_FIX),
        ("Add memory TTL support", ResearchTaskType.FEATURE),
        ("Refactor the repository service", ResearchTaskType.REFACTOR),
        ("Reduce request latency benchmark", ResearchTaskType.PERFORMANCE),
        ("Compare three cache policies", ResearchTaskType.RESEARCH),
    ],
)
def test_task_type_inference_covers_supported_categories(
    goal: str,
    expected: ResearchTaskType,
) -> None:
    task_type, source, confidence, rationale = infer_task_type(goal)

    assert task_type is expected
    assert source == "inferred_text"
    assert confidence == 0.8
    assert expected.value in rationale


def test_issue_only_goal_keeps_legacy_bug_fix_contract() -> None:
    task_type, source, _, _ = infer_task_type(
        "https://github.com/owner/repo/issues/42",
    )

    assert task_type is ResearchTaskType.BUG_FIX
    assert source == "legacy_issue_default"


def test_task_spec_round_trip_is_deterministic() -> None:
    spec = build_task_spec(
        "Add memory TTL support",
        "# Plan\n\nTask Type: feature\n",
    )

    encoded = spec.to_json()
    restored = TaskSpec.from_json(encoded)

    assert restored == spec
    assert restored.to_json() == encoded
    assert restored.basis_hash == task_spec_basis(
        "Add memory TTL support",
        "# Plan\n\nTask Type: feature\n",
    )


def test_task_spec_rejects_unknown_schema_version() -> None:
    spec = build_task_spec("Fix a bug")
    payload = spec.to_dict()
    payload["schema_version"] = 99

    with pytest.raises(ValueError, match="schema version"):
        TaskSpec.from_dict(payload)


def test_resolve_task_spec_caches_and_refreshes_on_plan_change() -> None:
    context: dict = {}
    dialog = _dialog()

    first = resolve_task_spec(dialog, context)
    cached_json = context["task_spec"]
    second = resolve_task_spec(dialog, context)

    assert second == first
    assert context["task_spec"] == cached_json

    dialog.plan_markdown = "# Plan\n\nTask Type: feature\n"
    refreshed = resolve_task_spec(dialog, context)

    assert refreshed.task_type is ResearchTaskType.FEATURE
    assert refreshed.basis_hash != first.basis_hash
    assert context["task_spec"] == refreshed.to_json()


def test_bug_fix_contract_requires_failing_baseline() -> None:
    contract = validation_contract_for(ResearchTaskType.BUG_FIX)

    assert contract.baseline_expectation is BaselineExpectation.MUST_FAIL
    assert contract.candidate_must_pass is True
    assert contract.requires_focused_tests is True


def test_feature_contract_treats_baseline_as_informational() -> None:
    contract = validation_contract_for(ResearchTaskType.FEATURE)

    assert contract.baseline_expectation is BaselineExpectation.INFORMATIONAL
    assert contract.requires_acceptance_criteria is True


def test_refactor_contract_requires_behavior_invariants() -> None:
    contract = validation_contract_for(ResearchTaskType.REFACTOR)

    assert contract.baseline_expectation is BaselineExpectation.MUST_PASS
    assert contract.requires_behavior_invariants is True


def test_performance_contract_requires_metric_evidence() -> None:
    contract = validation_contract_for(ResearchTaskType.PERFORMANCE)

    assert contract.requires_metric_evidence is True
    assert contract.baseline_expectation is BaselineExpectation.MUST_PASS


def test_research_contract_is_report_only() -> None:
    validation = validation_contract_for(ResearchTaskType.RESEARCH)
    delivery = delivery_contract_for(ResearchTaskType.RESEARCH)

    assert validation.requires_experiment_evidence is True
    assert validation.requires_focused_tests is False
    assert delivery.mode is DeliveryMode.REPORT_ONLY
    assert delivery.allow_auto_pr is False


@pytest.mark.parametrize(
    "task_type",
    list(ResearchTaskType),
)
def test_contract_instructions_name_exact_task_type(
    task_type: ResearchTaskType,
) -> None:
    spec = build_task_spec(
        "placeholder",
        f"Task Type: {task_type.value}\n",
    )

    instructions = task_contract_instructions(spec)

    assert f"Task Type: {task_type.value}" in instructions
    assert f"Delivery Mode: {spec.delivery.mode.value}" in instructions


def test_real_router_exposes_task_spec_service() -> None:
    from qwenpaw.app.routers import research as research_module

    assert research_module.ResearchTaskType is ResearchTaskType
    assert research_module.TaskSpec is TaskSpec
    assert research_module._task_spec_runtime_installed is True
    assert research_module._build_task_spec is build_task_spec
