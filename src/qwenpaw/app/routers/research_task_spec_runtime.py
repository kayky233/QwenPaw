"""Runtime adapters that bind TaskSpec to existing AutoResearch services."""

from __future__ import annotations

import contextvars
import re
from pathlib import Path
from types import ModuleType
from typing import Any

from . import research_delivery_service as delivery_service
from . import research_dialog_persistence as dialog_persistence
from . import research_validation_pipeline as validation_pipeline
from . import research_worktree_service as worktree_service
from .research_task_spec import (
    BaselineExpectation,
    DeliveryMode,
    ResearchTaskType,
    TaskSpec,
    resolve_task_spec,
    task_contract_instructions,
)

_ISSUE_RE = re.compile(r"(?:issues?/|#)(\d{1,10})", re.IGNORECASE)
_ACTIVE_TASK_SPEC: contextvars.ContextVar[TaskSpec | None] = contextvars.ContextVar(
    "qwenpaw_research_task_spec",
    default=None,
)


def _runtime_context_for_dialog(
    research_module: ModuleType,
    dialog: Any,
) -> dict[str, Any] | None:
    """Return runtime context only for real persisted dialog objects."""

    plan_id = getattr(dialog, "plan_id", "")
    if not isinstance(plan_id, str) or not plan_id.strip():
        return None
    return research_module._dialog_runtime_context.setdefault(plan_id, {})


def _explicit_operational_spec(
    dialog: Any,
    runtime_context: dict[str, Any],
) -> TaskSpec | None:
    """Return a spec only after the plan explicitly declares its task type."""

    spec = resolve_task_spec(dialog, runtime_context)
    return spec if spec.source == "explicit_plan" else None


def _issue_number(dialog: Any) -> str | None:
    text = f"{getattr(dialog, 'plan_markdown', '') or ''}\n"
    text += str(getattr(dialog, "goal", "") or "")
    match = _ISSUE_RE.search(text)
    return match.group(1) if match else None


def _normalized_goal(dialog: Any) -> str:
    goal = " ".join(str(getattr(dialog, "goal", "") or "").split())[:180]
    if not goal:
        raise RuntimeError("Cannot deliver an AutoResearch task with an empty goal")
    return goal


def _task_aware_title(dialog: Any, spec: TaskSpec) -> str:
    """Build a conventional title from an explicit task contract."""

    issue = _issue_number(dialog)
    prefix = spec.delivery.pull_request_prefix
    if issue is not None:
        actions = {
            ResearchTaskType.FEATURE: "implement",
            ResearchTaskType.REFACTOR: "refactor",
            ResearchTaskType.PERFORMANCE: "improve",
            ResearchTaskType.RESEARCH: "investigate",
        }
        action = actions.get(spec.task_type, "resolve")
        return f"{prefix}: {action} issue #{issue}"
    return f"{prefix}: {_normalized_goal(dialog)}"


def _task_aware_commit_message(dialog: Any, spec: TaskSpec) -> str:
    """Build the commit message paired with an explicit task contract."""

    issue = _issue_number(dialog)
    prefix = spec.delivery.commit_prefix
    if issue is not None:
        actions = {
            ResearchTaskType.FEATURE: "implement",
            ResearchTaskType.REFACTOR: "refactor",
            ResearchTaskType.PERFORMANCE: "improve",
            ResearchTaskType.RESEARCH: "investigate",
        }
        action = actions.get(spec.task_type, "resolve")
        return f"{prefix}: {action} issue #{issue}"
    return f"{prefix}: {_normalized_goal(dialog)}"


def task_validation_decision(
    spec: TaskSpec,
    reproduction_status: str,
    verification_status: str,
) -> tuple[bool, bool, str]:
    """Apply the evidence gate declared by one explicit TaskSpec."""

    if reproduction_status == "error" or verification_status == "error":
        return False, False, "task_validation_error"
    if spec.delivery.mode is DeliveryMode.REPORT_ONLY:
        return False, True, "research_report_required"
    if verification_status != "passed":
        return False, True, "candidate_validation_failed"
    if spec.validation.requires_metric_evidence:
        return False, True, "performance_evidence_required"

    baseline = spec.validation.baseline_expectation
    if baseline is BaselineExpectation.MUST_FAIL:
        if reproduction_status == "reproduced":
            return True, False, ""
        return False, True, "not_reproduced"
    if baseline is BaselineExpectation.MUST_PASS:
        if reproduction_status == "not_reproduced":
            return True, False, ""
        return False, True, "baseline_regression_detected"
    return True, False, ""


def _report_only_result(
    *,
    changed_paths: list[str],
    task_spec: TaskSpec,
) -> dict[str, Any]:
    task_type = task_spec.task_type.value
    changed = "\n".join(f"- `{path}`" for path in changed_paths) or "- none"
    return {
        "ready_to_commit": False,
        "recoverable": True,
        "failure_category": "research_report_required",
        "commit_sha": "",
        "test_summary": "",
        "changed_paths": changed_paths,
        "unapproved_paths": [],
        "reproduction_status": "not_required",
        "reproduction_summary": (
            "Baseline reproduction is not required for a report-only research task."
        ),
        "verification_status": "blocked",
        "verification_summary": (
            "A versioned experiment and artifact contract is required before delivery."
        ),
        "validation_report": f"""# AutoResearch Validation Report

## Status

Task contract requires report-only delivery. No commit, push, or pull request was
created.

## Task Type

`{task_type}`

## Changed Paths

{changed}

## Required Next Evidence

Provide hypotheses, an experiment matrix, reproducible evidence, artifacts, and
a decision record. Convert any accepted implementation direction into a new
feature, bug-fix, refactor, or performance task before source delivery.
""",
    }


async def _validate_report_only_scope(
    research_module: ModuleType,
    worktree: Path,
    dialog: Any,
    spec: TaskSpec,
) -> dict[str, Any]:
    """Preserve scope safety while blocking premature research delivery."""

    await research_module._run_process(
        ["git", "diff", "--check"],
        cwd=worktree,
        timeout=60,
    )
    status = await research_module._run_process(
        ["git", "status", "--porcelain"],
        cwd=worktree,
        timeout=30,
    )
    changed_paths = [
        path
        for path in research_module._expanded_changed_paths(
            worktree,
            research_module._changed_paths_from_porcelain(status),
        )
        if not research_module._is_ignored_research_runtime_path(path)
    ]
    approved = research_module._approved_plan_paths(
        getattr(dialog, "plan_markdown", "") or "",
    )
    unapproved = [path for path in changed_paths if path not in approved]
    if unapproved:
        return validation_pipeline.scope_mismatch_result(
            changed_paths,
            unapproved,
        )
    return _report_only_result(
        changed_paths=changed_paths,
        task_spec=spec,
    )


def install_research_task_spec_runtime(research_module: ModuleType) -> None:
    """Bind explicit TaskSpecs to old service seams without changing Router API."""

    dialog_persistence._PERSISTED_RUNTIME_KEYS = frozenset(
        {
            *dialog_persistence._PERSISTED_RUNTIME_KEYS,
            "task_spec",
        },
    )

    base_plan_prompt = research_module._build_plan_only_prompt

    def plan_prompt(
        goal: str,
        brief: Any = None,
        issue_evidence: str = "",
    ) -> str:
        prompt = base_plan_prompt(goal, brief, issue_evidence)
        spec = research_module._build_task_spec(goal)
        marker = "<<<FILE:program.md>>>\n# Problem Title\n"
        replacement = (
            "<<<FILE:program.md>>>\n# Problem Title\n\n"
            f"Task Type: {spec.task_type.value}\n"
        )
        if marker in prompt:
            prompt = prompt.replace(marker, replacement, 1)
        return prompt

    research_module._build_plan_only_prompt = plan_prompt

    base_execution_prompt = research_module._execution_prompt

    def execution_prompt(dialog: Any, worktree: Path) -> str:
        runtime_context = _runtime_context_for_dialog(research_module, dialog)
        if runtime_context is None:
            return base_execution_prompt(dialog, worktree)
        spec = resolve_task_spec(dialog, runtime_context)
        return (
            base_execution_prompt(dialog, worktree)
            + "\n\n"
            + task_contract_instructions(spec)
            + "\nFollow this contract even when prior issue-oriented instructions differ."
        )

    research_module._execution_prompt = execution_prompt

    legacy_branch_builder = worktree_service.build_research_branch

    def build_research_branch(dialog: Any) -> str:
        runtime_context = _runtime_context_for_dialog(research_module, dialog)
        if runtime_context is None:
            return legacy_branch_builder(dialog)
        spec = _explicit_operational_spec(dialog, runtime_context)
        if spec is None or spec.task_type is ResearchTaskType.BUG_FIX:
            return legacy_branch_builder(dialog)
        issue = _issue_number(dialog) or "task"
        suffix = (
            re.sub(r"[^a-z0-9]+", "", dialog.plan_id.lower())[:8]
            or "run"
        )
        return worktree_service.validate_git_branch_name(
            f"autoresearch/{spec.delivery.branch_prefix}-{issue}-{suffix}",
        )

    worktree_service.build_research_branch = build_research_branch
    research_module._build_research_branch = build_research_branch

    legacy_pr_title = delivery_service.dialog_pr_title

    def dialog_pr_title(dialog: Any) -> str:
        runtime_context = _runtime_context_for_dialog(research_module, dialog)
        if runtime_context is None:
            return legacy_pr_title(dialog)
        spec = _explicit_operational_spec(dialog, runtime_context)
        if spec is None or spec.task_type is ResearchTaskType.BUG_FIX:
            return legacy_pr_title(dialog)
        if not spec.delivery.allow_auto_pr:
            raise RuntimeError(
                "The approved task contract is report-only and cannot create a PR",
            )
        return _task_aware_title(dialog, spec)

    delivery_service.dialog_pr_title = dialog_pr_title
    research_module._dialog_pr_title = dialog_pr_title

    legacy_commit_message = validation_pipeline.research_commit_message

    def research_commit_message(dialog: Any) -> str:
        runtime_context = _runtime_context_for_dialog(research_module, dialog)
        if runtime_context is None:
            return legacy_commit_message(dialog)
        spec = _explicit_operational_spec(dialog, runtime_context)
        if spec is None or spec.task_type is ResearchTaskType.BUG_FIX:
            return legacy_commit_message(dialog)
        return _task_aware_commit_message(dialog, spec)

    validation_pipeline.research_commit_message = research_commit_message
    research_module._research_commit_message = research_commit_message

    legacy_decision = validation_pipeline.validation_decision

    def validation_decision(
        reproduction_status: str,
        verification_status: str,
    ) -> tuple[bool, bool, str]:
        spec = _ACTIVE_TASK_SPEC.get()
        if spec is None or spec.task_type is ResearchTaskType.BUG_FIX:
            return legacy_decision(reproduction_status, verification_status)
        return task_validation_decision(
            spec,
            reproduction_status,
            verification_status,
        )

    validation_pipeline.validation_decision = validation_decision
    research_module._validation_decision = validation_decision

    base_validate = research_module._validate_and_commit_worktree

    async def validate_and_commit_worktree(
        worktree: Path,
        dialog: Any,
    ) -> dict[str, Any]:
        runtime_context = _runtime_context_for_dialog(research_module, dialog)
        if runtime_context is None:
            return await base_validate(worktree, dialog)
        spec = _explicit_operational_spec(dialog, runtime_context)
        if spec is None:
            return await base_validate(worktree, dialog)
        if spec.delivery.mode is DeliveryMode.REPORT_ONLY:
            return await _validate_report_only_scope(
                research_module,
                worktree,
                dialog,
                spec,
            )
        token = _ACTIVE_TASK_SPEC.set(spec)
        try:
            return await base_validate(worktree, dialog)
        finally:
            _ACTIVE_TASK_SPEC.reset(token)

    research_module._task_validation_decision = task_validation_decision
    research_module._validate_and_commit_worktree = validate_and_commit_worktree
    research_module._task_spec_runtime_installed = True
