from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from qwenpaw.app.routers.research_dialog_persistence import (
    serializable_runtime_context,
)
from qwenpaw.app.routers.research_task_spec import (
    ResearchTaskType,
    build_task_spec,
)
from qwenpaw.app.routers.research_task_spec_runtime import (
    task_validation_decision,
)


def _spec(task_type: ResearchTaskType):
    return build_task_spec(
        "Placeholder task",
        f"# Plan\n\nTask Type: {task_type.value}\n",
    )


@pytest.mark.parametrize(
    ("task_type", "reproduction", "verification", "expected"),
    [
        (
            ResearchTaskType.BUG_FIX,
            "reproduced",
            "passed",
            (True, False, ""),
        ),
        (
            ResearchTaskType.BUG_FIX,
            "not_reproduced",
            "passed",
            (False, True, "not_reproduced"),
        ),
        (
            ResearchTaskType.FEATURE,
            "reproduced",
            "passed",
            (True, False, ""),
        ),
        (
            ResearchTaskType.FEATURE,
            "not_reproduced",
            "passed",
            (True, False, ""),
        ),
        (
            ResearchTaskType.REFACTOR,
            "not_reproduced",
            "passed",
            (True, False, ""),
        ),
        (
            ResearchTaskType.REFACTOR,
            "reproduced",
            "passed",
            (False, True, "baseline_regression_detected"),
        ),
        (
            ResearchTaskType.PERFORMANCE,
            "not_reproduced",
            "passed",
            (False, True, "performance_evidence_required"),
        ),
        (
            ResearchTaskType.RESEARCH,
            "not_required",
            "blocked",
            (False, True, "research_report_required"),
        ),
    ],
)
def test_task_validation_decision_uses_type_specific_contract(
    task_type: ResearchTaskType,
    reproduction: str,
    verification: str,
    expected: tuple[bool, bool, str],
) -> None:
    assert task_validation_decision(
        _spec(task_type),
        reproduction,
        verification,
    ) == expected


def test_task_validation_errors_are_nonrecoverable() -> None:
    assert task_validation_decision(
        _spec(ResearchTaskType.FEATURE),
        "error",
        "passed",
    ) == (False, False, "task_validation_error")


def test_plan_prompt_places_task_type_inside_program_template() -> None:
    from qwenpaw.app.routers import research as research_module

    prompt = research_module._build_plan_only_prompt(
        "Add memory TTL support",
    )

    assert (
        "<<<FILE:program.md>>>\n# Problem Title\n\nTask Type: feature\n"
        in prompt
    )
    assert "Delivery Mode: pull_request" in prompt


def test_explicit_feature_changes_branch_pr_and_commit_prefixes() -> None:
    from qwenpaw.app.routers import research as research_module

    plan_id = "plan-feature-runtime"
    dialog = SimpleNamespace(
        plan_id=plan_id,
        goal="Add memory TTL support for issue #42",
        plan_markdown="# Plan\n\nTask Type: feature\nIssue: #42\n",
    )
    try:
        assert research_module._build_research_branch(dialog) == (
            "autoresearch/feature-42-planfeat"
        )
        assert research_module._dialog_pr_title(dialog) == (
            "feat: implement issue #42"
        )
        assert research_module._research_commit_message(dialog) == (
            "feat: implement issue #42"
        )
        cached = research_module._dialog_runtime_context[plan_id]["task_spec"]
        assert '"task_type":"feature"' in cached
    finally:
        research_module._dialog_runtime_context.pop(plan_id, None)


def test_explicit_refactor_and_performance_use_conventional_prefixes() -> None:
    from qwenpaw.app.routers import research as research_module

    refactor = SimpleNamespace(
        plan_id="plan-refactor-runtime",
        goal="Refactor repository service",
        plan_markdown="# Plan\n\nTask Type: refactor\n",
    )
    performance = SimpleNamespace(
        plan_id="plan-performance-runtime",
        goal="Reduce request latency",
        plan_markdown="# Plan\n\nTask Type: performance\n",
    )
    try:
        assert research_module._build_research_branch(refactor).startswith(
            "autoresearch/refactor-task-",
        )
        assert research_module._research_commit_message(refactor).startswith(
            "refactor:",
        )
        assert research_module._build_research_branch(performance).startswith(
            "autoresearch/performance-task-",
        )
        assert research_module._dialog_pr_title(performance).startswith(
            "perf:",
        )
    finally:
        research_module._dialog_runtime_context.pop(
            refactor.plan_id,
            None,
        )
        research_module._dialog_runtime_context.pop(
            performance.plan_id,
            None,
        )


def test_legacy_helpers_without_plan_id_keep_historical_behavior() -> None:
    from qwenpaw.app.routers import research as research_module

    dialog = SimpleNamespace(
        goal="Develop memory TTL",
        plan_markdown="# Feature",
    )

    assert research_module._research_commit_message(dialog) == (
        "fix: resolve issue #research task"
    )


def test_research_task_refuses_pull_request_creation() -> None:
    from qwenpaw.app.routers import research as research_module

    dialog = SimpleNamespace(
        plan_id="plan-report-only",
        goal="Compare cache policies",
        plan_markdown="# Plan\n\nTask Type: research\n",
    )
    try:
        with pytest.raises(RuntimeError, match="report-only"):
            research_module._dialog_pr_title(dialog)
    finally:
        research_module._dialog_runtime_context.pop(dialog.plan_id, None)


def test_execution_prompt_contains_resolved_task_contract(tmp_path: Path) -> None:
    from qwenpaw.app.routers import research as research_module

    dialog = SimpleNamespace(
        plan_id="plan-execution-contract",
        goal="Refactor repository service",
        plan_markdown="# Plan\n\nTask Type: refactor\n",
        validation_attempts=[],
        validation_report="",
        unapproved_paths=[],
        approved_revision=1,
        approved_content_hash="a" * 64,
    )
    try:
        prompt = research_module._execution_prompt(dialog, tmp_path)

        assert "Task Type: refactor" in prompt
        assert "Behavior Invariants" in prompt
        assert "baseline and candidate focused tests must both pass" in (
            prompt.lower()
        )
    finally:
        research_module._dialog_runtime_context.pop(dialog.plan_id, None)


def test_task_spec_is_included_in_serializable_runtime_context() -> None:
    spec = _spec(ResearchTaskType.FEATURE)

    serialized = serializable_runtime_context(
        {
            "task_spec": spec.to_json(),
            "base_branch": "develop",
            "workspace": object(),
        },
    )

    assert serialized == {
        "task_spec": spec.to_json(),
        "base_branch": "develop",
    }


@pytest.mark.asyncio
async def test_feature_pipeline_allows_passing_baseline_and_candidate(
    tmp_path: Path,
) -> None:
    from qwenpaw.app.routers import research as research_module

    plan_id = "plan-feature-validation"
    dialog = SimpleNamespace(
        plan_id=plan_id,
        goal="Add memory TTL support",
        plan_markdown=(
            "# Feature\n\n"
            "Task Type: feature\n\n"
            "## Modifiable Files\n"
            "- src/memory.py\n"
            "- tests/unit/test_memory_ttl.py\n\n"
            "## Reproduction\n"
            "Baseline Ref: HEAD\n"
        ),
        upstream_repository="owner/repository",
    )
    run_process = AsyncMock(
        side_effect=[
            "",
            " M src/memory.py\n M tests/unit/test_memory_ttl.py",
            "",
            "",
            "",
            "deadbeef\n",
        ],
    )
    run_tests = AsyncMock(
        side_effect=[
            [
                SimpleNamespace(
                    command="pytest tests/unit/test_memory_ttl.py",
                    exit_code=0,
                    output="1 passed",
                ),
            ],
            [
                SimpleNamespace(
                    command="pytest tests/unit/test_memory_ttl.py",
                    exit_code=0,
                    output="1 passed",
                ),
            ],
        ],
    )
    original_run_process = research_module._run_process
    original_prepare = research_module._prepare_reproduction_baseline
    original_tests = research_module._run_changed_research_tests
    original_report = research_module._build_validation_report
    research_module._run_process = run_process
    research_module._prepare_reproduction_baseline = AsyncMock(
        return_value=tmp_path / "baseline",
    )
    research_module._run_changed_research_tests = run_tests
    research_module._build_validation_report = MagicMock(
        return_value="# Feature validation",
    )
    try:
        result = await research_module._validate_and_commit_worktree(
            tmp_path,
            dialog,
        )
    finally:
        research_module._run_process = original_run_process
        research_module._prepare_reproduction_baseline = original_prepare
        research_module._run_changed_research_tests = original_tests
        research_module._build_validation_report = original_report
        research_module._dialog_runtime_context.pop(plan_id, None)

    assert result["ready_to_commit"] is True
    assert result["reproduction_status"] == "not_reproduced"
    assert result["verification_status"] == "passed"
    commit_call = next(
        call
        for call in run_process.await_args_list
        if call.args[0][:2] == ["git", "commit"]
    )
    assert commit_call.args[0][-1] == "feat: Add memory TTL support"


@pytest.mark.asyncio
async def test_research_pipeline_returns_report_only_result(
    tmp_path: Path,
) -> None:
    from qwenpaw.app.routers import research as research_module

    plan_id = "plan-research-validation"
    dialog = SimpleNamespace(
        plan_id=plan_id,
        goal="Compare cache policies",
        plan_markdown=(
            "# Research\n\n"
            "Task Type: research\n\n"
            "## Modifiable Files\n"
            "- docs/cache-policy-report.md\n"
        ),
    )
    run_process = AsyncMock(
        side_effect=[
            "",
            " M docs/cache-policy-report.md",
        ],
    )
    original_run_process = research_module._run_process
    research_module._run_process = run_process
    try:
        result = await research_module._validate_and_commit_worktree(
            tmp_path,
            dialog,
        )
    finally:
        research_module._run_process = original_run_process
        research_module._dialog_runtime_context.pop(plan_id, None)

    assert result["ready_to_commit"] is False
    assert result["failure_category"] == "research_report_required"
    assert result["reproduction_status"] == "not_required"
    assert "report-only" in result["validation_report"]
    assert not any(
        call.args[0][:2] == ["git", "commit"]
        for call in run_process.await_args_list
    )
