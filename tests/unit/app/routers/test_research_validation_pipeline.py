from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, call

import pytest

from qwenpaw.app.routers.research_scope import approved_plan_paths
from qwenpaw.app.routers.research_validation_pipeline import (
    classify_reproduction,
    classify_verification,
    install_research_validation_pipeline,
    research_commit_message,
    scope_mismatch_result,
    summarize_executions,
    validate_and_commit_worktree,
    validation_decision,
    validation_result,
)
from qwenpaw.app.routers.research_validation_service import (
    changed_paths_from_porcelain,
    expanded_changed_paths,
    focused_test_paths,
    is_ignored_research_runtime_path,
    reproduction_baseline_ref,
)


def _execution(command: str, exit_code: int):
    return SimpleNamespace(command=command, exit_code=exit_code, output="")


def _dialog(**overrides):
    values = {
        "goal": "Fix https://github.com/agentscope-ai/QwenPaw/issues/42",
        "plan_markdown": """# Plan

## Modifiable Files
- `src/fix.py`
- `tests/unit/test_fix.py`

## Reproduction
Baseline Ref: `main`
""",
        "upstream_repository": "agentscope-ai/QwenPaw",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_reproduction_and_verification_classification() -> None:
    assert classify_reproduction([_execution("pytest", 1)]) == "reproduced"
    assert classify_reproduction([_execution("pytest", 0)]) == "not_reproduced"
    assert classify_reproduction([_execution("pytest", 2)]) == "error"
    assert classify_reproduction([]) == "error"

    assert classify_verification([_execution("pytest", 0)]) == "passed"
    assert classify_verification([_execution("pytest", 1)]) == "failed"
    assert classify_verification([_execution("pytest", 2)]) == "error"
    assert classify_verification([]) == "error"


def test_validation_decision_requires_reproduced_baseline_and_passing_candidate() -> None:
    assert validation_decision("reproduced", "passed") == (True, False, "")
    assert validation_decision("not_reproduced", "passed") == (
        False,
        True,
        "not_reproduced",
    )
    assert validation_decision("reproduced", "failed") == (
        False,
        True,
        "candidate_validation_failed",
    )
    assert validation_decision("error", "passed") == (
        False,
        False,
        "candidate_validation_failed",
    )


def test_summarize_executions_is_stable() -> None:
    assert summarize_executions(
        [_execution("pytest tests/test_fix.py", 1), _execution("ruff check", 0)],
    ) == "pytest tests/test_fix.py -> exit 1; ruff check -> exit 0"


def test_scope_mismatch_result_preserves_exact_paths() -> None:
    result = scope_mismatch_result(
        ["src/fix.py", "src/unapproved.py"],
        ["src/unapproved.py"],
    )

    assert result["ready_to_commit"] is False
    assert result["recoverable"] is True
    assert result["failure_category"] == "scope_mismatch"
    assert result["unapproved_paths"] == ["src/unapproved.py"]
    assert "Baseline reproduction and candidate verification were not run" in (
        result["validation_report"]
    )


def test_validation_result_carries_decision_and_report() -> None:
    result = validation_result(
        changed_paths=["src/fix.py", "tests/unit/test_fix.py"],
        reproduction_status="reproduced",
        reproduction_summary="baseline -> exit 1",
        verification_status="passed",
        verification_summary="candidate -> exit 0",
        validation_report="# Report",
    )

    assert result["ready_to_commit"] is True
    assert result["recoverable"] is False
    assert result["failure_category"] == ""
    assert result["test_summary"] == "candidate -> exit 0"


def test_research_commit_message_uses_issue_or_task_fallback() -> None:
    assert research_commit_message(_dialog()) == "fix: resolve issue #42"
    assert research_commit_message(
        _dialog(goal="Develop memory TTL", plan_markdown="# Feature"),
    ) == "fix: resolve issue #research task"


@pytest.mark.asyncio
async def test_pipeline_blocks_unapproved_paths_before_tests(
    tmp_path: Path,
) -> None:
    status = " M src/fix.py\n M src/unapproved.py"
    run_process = AsyncMock(side_effect=["", status])
    prepare_baseline = AsyncMock()
    run_tests = AsyncMock()

    result = await validate_and_commit_worktree(
        tmp_path,
        _dialog(),
        run_process=run_process,
        changed_paths_from_porcelain=changed_paths_from_porcelain,
        expanded_changed_paths=expanded_changed_paths,
        is_ignored_runtime_path=is_ignored_research_runtime_path,
        approved_plan_paths=approved_plan_paths,
        focused_test_paths=focused_test_paths,
        reproduction_baseline_ref=reproduction_baseline_ref,
        prepare_reproduction_baseline=prepare_baseline,
        run_changed_tests=run_tests,
        build_validation_report=MagicMock(),
        environment={},
    )

    assert result["failure_category"] == "scope_mismatch"
    assert result["unapproved_paths"] == ["src/unapproved.py"]
    prepare_baseline.assert_not_awaited()
    run_tests.assert_not_awaited()


@pytest.mark.asyncio
async def test_pipeline_commits_only_after_evidence_gate_passes(
    tmp_path: Path,
) -> None:
    status = " M src/fix.py\n M tests/unit/test_fix.py"
    run_process = AsyncMock(
        side_effect=["", status, "", "", "", "deadbeef\n"],
    )
    baseline = tmp_path / "baseline"
    prepare_baseline = AsyncMock(return_value=baseline)
    run_tests = AsyncMock(
        side_effect=[
            [_execution("pytest tests/unit/test_fix.py", 1)],
            [_execution("pytest tests/unit/test_fix.py", 0)],
        ],
    )
    build_report = MagicMock(return_value="# Validation report")

    result = await validate_and_commit_worktree(
        tmp_path,
        _dialog(),
        run_process=run_process,
        changed_paths_from_porcelain=changed_paths_from_porcelain,
        expanded_changed_paths=expanded_changed_paths,
        is_ignored_runtime_path=is_ignored_research_runtime_path,
        approved_plan_paths=approved_plan_paths,
        focused_test_paths=focused_test_paths,
        reproduction_baseline_ref=reproduction_baseline_ref,
        prepare_reproduction_baseline=prepare_baseline,
        run_changed_tests=run_tests,
        build_validation_report=build_report,
        environment={"PATH": "/usr/bin"},
    )

    assert result["ready_to_commit"] is True
    assert result["commit_sha"] == "deadbeef"
    assert result["reproduction_status"] == "reproduced"
    assert result["verification_status"] == "passed"
    assert call(
        [
            "git",
            "add",
            "--",
            "src/fix.py",
            "tests/unit/test_fix.py",
        ],
        cwd=tmp_path,
        timeout=30,
    ) in run_process.await_args_list
    commit_call = next(
        item
        for item in run_process.await_args_list
        if item.args[0][:2] == ["git", "commit"]
    )
    assert commit_call.args[0][-1] == "fix: resolve issue #42"
    assert commit_call.kwargs["env"]["GIT_AUTHOR_NAME"] == (
        "QwenPaw AutoResearch"
    )
    assert commit_call.kwargs["env"]["PATH"] == "/usr/bin"


@pytest.mark.asyncio
async def test_pipeline_returns_recoverable_not_reproduced_without_commit(
    tmp_path: Path,
) -> None:
    status = " M src/fix.py\n M tests/unit/test_fix.py"
    run_process = AsyncMock(side_effect=["", status])
    run_tests = AsyncMock(
        side_effect=[
            [_execution("pytest tests/unit/test_fix.py", 0)],
            [_execution("pytest tests/unit/test_fix.py", 0)],
        ],
    )

    result = await validate_and_commit_worktree(
        tmp_path,
        _dialog(),
        run_process=run_process,
        changed_paths_from_porcelain=changed_paths_from_porcelain,
        expanded_changed_paths=expanded_changed_paths,
        is_ignored_runtime_path=is_ignored_research_runtime_path,
        approved_plan_paths=approved_plan_paths,
        focused_test_paths=focused_test_paths,
        reproduction_baseline_ref=reproduction_baseline_ref,
        prepare_reproduction_baseline=AsyncMock(
            return_value=tmp_path / "baseline",
        ),
        run_changed_tests=run_tests,
        build_validation_report=MagicMock(return_value="# Report"),
        environment={},
    )

    assert result["ready_to_commit"] is False
    assert result["recoverable"] is True
    assert result["failure_category"] == "not_reproduced"
    assert len(run_process.await_args_list) == 2


@pytest.mark.asyncio
async def test_pipeline_treats_test_infrastructure_exit_as_nonrecoverable(
    tmp_path: Path,
) -> None:
    status = " M src/fix.py\n M tests/unit/test_fix.py"
    run_process = AsyncMock(side_effect=["", status])
    run_tests = AsyncMock(
        side_effect=[
            [_execution("pytest tests/unit/test_fix.py", 2)],
            [_execution("pytest tests/unit/test_fix.py", 0)],
        ],
    )

    result = await validate_and_commit_worktree(
        tmp_path,
        _dialog(),
        run_process=run_process,
        changed_paths_from_porcelain=changed_paths_from_porcelain,
        expanded_changed_paths=expanded_changed_paths,
        is_ignored_runtime_path=is_ignored_research_runtime_path,
        approved_plan_paths=approved_plan_paths,
        focused_test_paths=focused_test_paths,
        reproduction_baseline_ref=reproduction_baseline_ref,
        prepare_reproduction_baseline=AsyncMock(
            return_value=tmp_path / "baseline",
        ),
        run_changed_tests=run_tests,
        build_validation_report=MagicMock(return_value="# Report"),
        environment={},
    )

    assert result["ready_to_commit"] is False
    assert result["recoverable"] is False
    assert result["reproduction_status"] == "error"


def test_installer_exposes_pipeline_helpers() -> None:
    module = SimpleNamespace(
        _run_process=AsyncMock(),
        _changed_paths_from_porcelain=changed_paths_from_porcelain,
        _expanded_changed_paths=expanded_changed_paths,
        _is_ignored_research_runtime_path=is_ignored_research_runtime_path,
        _approved_plan_paths=approved_plan_paths,
        _focused_test_paths=focused_test_paths,
        _reproduction_baseline_ref=reproduction_baseline_ref,
        _prepare_reproduction_baseline=AsyncMock(),
        _run_changed_research_tests=AsyncMock(),
        _build_validation_report=MagicMock(),
    )

    install_research_validation_pipeline(module)

    assert module._classify_reproduction is classify_reproduction
    assert module._classify_verification is classify_verification
    assert module._validation_decision is validation_decision


def test_real_router_preserves_validation_pipeline_behavior() -> None:
    from qwenpaw.app.routers import research as research_module

    reproduction = [_execution("pytest", 1)]
    assert research_module._classify_reproduction(reproduction) == "reproduced"
    assert research_module._validation_decision(
        "reproduced",
        "passed",
    ) == (True, False, "")
    assert callable(research_module._validate_and_commit_worktree)
