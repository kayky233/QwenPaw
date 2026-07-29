"""Validation and commit pipeline for supervised AutoResearch worktrees."""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path
from types import ModuleType
from typing import Any, Awaitable, Callable, Mapping

RunProcess = Callable[..., Awaitable[str]]

_ISSUE_NUMBER_RE = re.compile(r"(?:issues?/|#)(\d{1,10})", re.IGNORECASE)


def classify_reproduction(executions: list[Any]) -> str:
    """Classify whether focused tests reproduced the approved baseline issue."""

    codes = [execution.exit_code for execution in executions]
    if not codes or any(code not in {0, 1} for code in codes):
        return "error"
    if any(code == 1 for code in codes):
        return "reproduced"
    return "not_reproduced"


def classify_verification(executions: list[Any]) -> str:
    """Classify whether the candidate passed all focused tests."""

    codes = [execution.exit_code for execution in executions]
    if not codes or any(code not in {0, 1} for code in codes):
        return "error"
    if all(code == 0 for code in codes):
        return "passed"
    return "failed"


def summarize_executions(executions: list[Any]) -> str:
    """Create a stable command/exit-code summary for reports and UI state."""

    return "; ".join(
        f"{execution.command} -> exit {execution.exit_code}"
        for execution in executions
    )


def validation_decision(
    reproduction_status: str,
    verification_status: str,
) -> tuple[bool, bool, str]:
    """Return ``ready``, ``recoverable`` and failure category."""

    ready = (
        reproduction_status == "reproduced"
        and verification_status == "passed"
    )
    recoverable = (
        not ready
        and reproduction_status != "error"
        and verification_status != "error"
    )
    if ready:
        category = ""
    elif reproduction_status == "not_reproduced":
        category = "not_reproduced"
    else:
        category = "candidate_validation_failed"
    return ready, recoverable, category


def scope_mismatch_result(
    changed_paths: list[str],
    unapproved_paths: list[str],
) -> dict[str, Any]:
    """Build a recoverable result before any tests or commit can run."""

    detail = (
        "Repository changes include paths not listed in the approved plan: "
        + ", ".join(unapproved_paths)
    )
    changed_lines = "\n".join(f"- `{path}`" for path in changed_paths)
    unapproved_lines = "\n".join(
        f"- `{path}`" for path in unapproved_paths
    )
    return {
        "ready_to_commit": False,
        "recoverable": True,
        "failure_category": "scope_mismatch",
        "commit_sha": "",
        "test_summary": "",
        "changed_paths": changed_paths,
        "unapproved_paths": unapproved_paths,
        "reproduction_status": "pending",
        "reproduction_summary": (
            "Validation did not start because the approved file scope "
            "does not cover all repository changes."
        ),
        "verification_status": "blocked",
        "verification_summary": (
            "Revise and reapprove the plan before validation continues."
        ),
        "validation_report": f"""# AutoResearch Validation Report

## Status

Needs plan revision. No commit, push, or pull request was created.

## Changed Paths

{changed_lines}

## Paths Outside Approved Plan

{unapproved_lines}

## Validation Result

{detail}

Baseline reproduction and candidate verification were not run. The existing
worktree and all changes were preserved so research can continue after the
revised plan is approved.
""",
    }


def validation_result(
    *,
    changed_paths: list[str],
    reproduction_status: str,
    reproduction_summary: str,
    verification_status: str,
    verification_summary: str,
    validation_report: str,
) -> dict[str, Any]:
    """Build the structured evidence gate result before optional commit."""

    ready, recoverable, category = validation_decision(
        reproduction_status,
        verification_status,
    )
    return {
        "ready_to_commit": ready,
        "recoverable": recoverable,
        "failure_category": category,
        "commit_sha": "",
        "test_summary": verification_summary,
        "changed_paths": changed_paths,
        "unapproved_paths": [],
        "reproduction_status": reproduction_status,
        "reproduction_summary": reproduction_summary,
        "verification_status": verification_status,
        "verification_summary": verification_summary,
        "validation_report": validation_report,
    }


def research_commit_message(dialog: Any) -> str:
    """Build the current issue-oriented commit message."""

    match = _ISSUE_NUMBER_RE.search(
        f"{dialog.plan_markdown or ''}\n{dialog.goal}",
    )
    issue = match.group(1) if match else "research task"
    return f"fix: resolve issue #{issue}"


async def validate_and_commit_worktree(
    worktree: Path,
    dialog: Any,
    *,
    run_process: RunProcess,
    changed_paths_from_porcelain: Callable[[str], list[str]],
    expanded_changed_paths: Callable[[Path, list[str]], list[str]],
    is_ignored_runtime_path: Callable[[str], bool],
    approved_plan_paths: Callable[[str], set[str]],
    focused_test_paths: Callable[[list[str]], list[str]],
    reproduction_baseline_ref: Callable[[str], str],
    prepare_reproduction_baseline: Callable[..., Awaitable[Path]],
    run_changed_tests: Callable[..., Awaitable[list[Any]]],
    build_validation_report: Callable[..., str],
    environment: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Run the evidence gate and commit only a proven candidate."""

    await run_process(["git", "diff", "--check"], cwd=worktree, timeout=60)
    status = await run_process(
        ["git", "status", "--porcelain"],
        cwd=worktree,
        timeout=30,
    )
    if not status.strip():
        raise RuntimeError("Agent completed without changing repository files")

    changed_paths = [
        path
        for path in expanded_changed_paths(
            worktree,
            changed_paths_from_porcelain(status),
        )
        if not is_ignored_runtime_path(path)
    ]
    if not changed_paths:
        raise RuntimeError("Agent completed without changing repository files")

    approved_paths = approved_plan_paths(dialog.plan_markdown or "")
    unapproved_paths = [
        path for path in changed_paths if path not in approved_paths
    ]
    if unapproved_paths:
        return scope_mismatch_result(changed_paths, unapproved_paths)

    test_paths = focused_test_paths(changed_paths)
    if not test_paths:
        raise RuntimeError(
            "Repository changes do not include a focused regression test",
        )

    baseline_ref = reproduction_baseline_ref(dialog.plan_markdown or "")
    with tempfile.TemporaryDirectory(prefix="qwenpaw-research-test-") as temp:
        test_root = Path(temp)
        baseline = await prepare_reproduction_baseline(
            worktree,
            test_root,
            test_paths,
            baseline_ref,
            dialog.upstream_repository,
        )
        baseline_executions = await run_changed_tests(
            baseline,
            test_paths,
            test_root / "baseline-runtime",
        )
        candidate_executions = await run_changed_tests(
            worktree,
            test_paths,
            test_root / "candidate-runtime",
        )

    reproduction_status = classify_reproduction(baseline_executions)
    verification_status = classify_verification(candidate_executions)
    reproduction_summary = summarize_executions(baseline_executions)
    verification_summary = summarize_executions(candidate_executions)
    report = build_validation_report(
        dialog,
        changed_paths,
        baseline_ref,
        baseline_executions,
        candidate_executions,
        reproduction_status,
        verification_status,
    )
    result = validation_result(
        changed_paths=changed_paths,
        reproduction_status=reproduction_status,
        reproduction_summary=reproduction_summary,
        verification_status=verification_status,
        verification_summary=verification_summary,
        validation_report=report,
    )
    if not result["ready_to_commit"]:
        return result

    await run_process(
        ["git", "add", "--", *changed_paths],
        cwd=worktree,
        timeout=30,
    )
    await run_process(
        ["git", "diff", "--cached", "--check"],
        cwd=worktree,
        timeout=60,
    )
    commit_env = {
        **(environment if environment is not None else os.environ),
        "GIT_AUTHOR_NAME": "QwenPaw AutoResearch",
        "GIT_AUTHOR_EMAIL": "autoresearch@qwenpaw.local",
        "GIT_COMMITTER_NAME": "QwenPaw AutoResearch",
        "GIT_COMMITTER_EMAIL": "autoresearch@qwenpaw.local",
    }
    await run_process(
        ["git", "commit", "-m", research_commit_message(dialog)],
        cwd=worktree,
        timeout=120,
        env=commit_env,
    )
    commit_sha = await run_process(
        ["git", "rev-parse", "HEAD"],
        cwd=worktree,
        timeout=30,
    )
    result["commit_sha"] = commit_sha.strip()
    return result


def install_research_validation_pipeline(research_module: ModuleType) -> None:
    """Install the extracted validation pipeline on the legacy router surface."""

    async def validate(worktree: Path, dialog: Any) -> dict[str, Any]:
        return await validate_and_commit_worktree(
            worktree,
            dialog,
            run_process=research_module._run_process,
            changed_paths_from_porcelain=(
                research_module._changed_paths_from_porcelain
            ),
            expanded_changed_paths=research_module._expanded_changed_paths,
            is_ignored_runtime_path=(
                research_module._is_ignored_research_runtime_path
            ),
            approved_plan_paths=research_module._approved_plan_paths,
            focused_test_paths=research_module._focused_test_paths,
            reproduction_baseline_ref=(
                research_module._reproduction_baseline_ref
            ),
            prepare_reproduction_baseline=(
                research_module._prepare_reproduction_baseline
            ),
            run_changed_tests=research_module._run_changed_research_tests,
            build_validation_report=research_module._build_validation_report,
        )

    research_module._classify_reproduction = classify_reproduction
    research_module._classify_verification = classify_verification
    research_module._summarize_executions = summarize_executions
    research_module._validation_decision = validation_decision
    research_module._scope_mismatch_result = scope_mismatch_result
    research_module._validation_result = validation_result
    research_module._research_commit_message = research_commit_message
    research_module._validate_and_commit_worktree = validate
