# -*- coding: utf-8 -*-
"""Portable AutoResearch task discovery and run APIs."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import os
import platform
import re
import shlex
import shutil
import sys
import tempfile
import uuid
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Awaitable, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request as UrlRequest, urlopen

_log = logging.getLogger(__name__)

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ..agent_context import get_current_agent_id
from ..agent_context import get_current_session_id
from ..agent_context import get_current_user_id
from ...cli.task_cmd import _run_task
from ...research import FATAL_OUTCOME_STATUSES, ResearchOutcome
from ...research import (
    evaluate_candidate,
    list_research_history,
    load_task,
    create_research_task,
)
from ...research import UNSAFE_RESEARCH_OPT_IN, unsafe_research_enabled
from ...constant import WORKING_DIR
from ...research_ledger.repository import (
    BaseResearchLedgerRepository,
    PostgresResearchLedgerRepository,
)
from ...research_runtime import run_with_qwenpaw as _run_with_qwenpaw

router = APIRouter(prefix="/research", tags=["research"])
_VALID_TASK_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_PLANNING_TAG_RE = re.compile(
    r"<<<FILE:(.+?)>>>\s*\n(.*?)<<<END>>>", re.DOTALL
)


class CreateResearchTask(BaseModel):
    task_id: str = Field(
        min_length=1, max_length=100, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$"
    )
    program: str = Field(min_length=1, max_length=100_000)
    solution_name: str = Field(min_length=1, max_length=50)
    solution_source: str = Field(min_length=1, max_length=100_000)
    judge_source: str = Field(min_length=1, max_length=100_000)


class StartResearchRun(BaseModel):
    rounds: int = Field(default=3, ge=1, le=100)
    agent_id: str = Field(default="default", min_length=1, max_length=100)
    model: str | None = Field(default=None, max_length=300)


class DialogGoalRequest(BaseModel):
    goal: str = Field(
        min_length=1,
        max_length=5000,
        description="Natural language research goal",
    )
    model: str | None = Field(default=None, max_length=300)
    rounds: int = Field(default=3, ge=1, le=100)
    auto_pr: bool = Field(
        default=True, description="Automatically create PR after research"
    )
    session_id: str | None = Field(default=None, min_length=1, max_length=300)
    user_id: str | None = Field(default=None, min_length=1, max_length=300)


class DialogPlanEditRequest(BaseModel):
    plan_markdown: str = Field(min_length=1, max_length=100_000)
    expected_revision: int = Field(ge=1)


class DialogPlanRevisionRequest(BaseModel):
    instruction: str = Field(default="", max_length=5000)
    expected_revision: int = Field(ge=1)


class DialogPlanRevisionDecisionRequest(BaseModel):
    expected_revision: int = Field(ge=1)


class DialogPlanApprovalRequest(BaseModel):
    expected_revision: int = Field(ge=1)
    content_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    idempotency_key: str = Field(min_length=8, max_length=200)


class DialogPlanRejectRequest(BaseModel):
    expected_revision: int = Field(ge=1)
    reason: str = Field(default="", max_length=2000)


class ResearchDirection(BaseModel):
    id: str = Field(min_length=1, max_length=100)
    title: str = Field(min_length=1, max_length=300)
    priority: int = Field(ge=1, le=100)
    risk: str = Field(min_length=1, max_length=50)
    reason: str = Field(min_length=1, max_length=1000)


class ResearchBrief(BaseModel):
    goal: str = Field(min_length=1, max_length=5000)
    current_behavior: str = Field(min_length=1, max_length=10_000)
    root_cause_hypotheses: list[str] = Field(min_length=1, max_length=20)
    candidate_directions: list[ResearchDirection] = Field(
        min_length=1, max_length=20
    )
    success_metrics: list[str] = Field(min_length=1, max_length=20)
    iteration_budget: int = Field(ge=1, le=100)
    modifiable_files: list[str] = Field(default_factory=list, max_length=100)
    relevant_tests: list[str] = Field(default_factory=list, max_length=100)


@dataclass(frozen=True)
class ResearchRunEvent:
    phase: str
    timestamp: str
    round: int | None = None
    detail: str = ""
    sequence: int = 0  # monotonic, for dedup & after_sequence resumption


@dataclass(frozen=True)
class SubmissionResult:
    """Published artifact metadata, currently used for optional PR creation."""

    status: str
    url: str = ""
    verdict: str = ""
    error: str = ""


# ── Terminal run statuses — completed runs should immediately close SSE ──
_TERMINAL_STATUSES: frozenset[str] = frozenset(
    {"completed", "failed", "cancelled", "auth_required"}
)


@dataclass
class DialogRunState:
    """Mutable state for the dialog discovery + approval pipeline."""

    plan_id: str
    status: str
    goal: str
    events: list[dict[str, Any]]
    task_id: str | None = None
    task_title: str | None = None
    run_id: str | None = None
    brief: dict[str, Any] | None = None
    plan_markdown: str | None = None
    owner_agent_id: str = "default"
    owner_user_id: str | None = None
    owner_session_id: str | None = None
    rounds: int = 3
    model: str | None = None
    auto_pr: bool = True
    revision: int = 0
    content_hash: str = ""
    approved_revision: int | None = None
    approved_content_hash: str = ""
    approval_idempotency_key: str = ""
    approved_by: str | None = None
    approved_at: str | None = None
    rejected_by: str | None = None
    rejected_at: str | None = None
    rejection_reason: str = ""
    upstream_repository: str = ""
    push_repository: str = ""
    worktree_path: str = ""
    branch: str = ""
    commit_sha: str = ""
    pr_url: str = ""
    test_summary: str = ""
    reproduction_status: str = "pending"
    reproduction_summary: str = ""
    verification_status: str = "pending"
    verification_summary: str = ""
    validation_report: str = ""
    changed_paths: list[str] = field(default_factory=list)
    unapproved_paths: list[str] = field(default_factory=list)
    validation_failure_category: str = ""
    validation_attempts: list[dict[str, Any]] = field(default_factory=list)
    revision_proposal: str = ""
    revision_proposal_reason: str = ""
    revision_proposal_revision: int | None = None
    current_environment: str = ""
    environment_compatibility: str = "unknown"
    environment_compatibility_reason: str = ""
    error: str = ""
    created_at: str = ""
    updated_at: str = ""


@dataclass(frozen=True)
class ResearchTestExecution:
    """One sandboxed test command and its captured evidence."""

    command: str
    exit_code: int
    output: str


@dataclass(frozen=True)
class ResearchRunState:
    id: str
    task_id: str
    agent_id: str
    owner_agent_id: str
    owner_user_id: str | None
    owner_session_id: str | None
    status: str
    rounds: int
    completed_rounds: int
    outcomes: tuple[ResearchOutcome, ...]
    created_at: str
    events: tuple[ResearchRunEvent, ...]
    current_round: int | None
    phase: str
    updated_at: str
    started_at: str | None = None
    finished_at: str | None = None
    error: str = ""
    submission: SubmissionResult | None = None
    research_brief: dict[str, Any] | None = None


_runs: dict[str, ResearchRunState] = {}
_runtime_tasks: dict[str, asyncio.Task[None]] = {}
_run_auto_pr: dict[str, bool] = {}
_dialog_runs: dict[str, DialogRunState] = {}
_dialog_tasks: dict[str, asyncio.Task[None]] = {}
_dialog_runtime_context: dict[str, dict[str, Any]] = {}
_dialog_sse_queues: dict[str, set[asyncio.Queue[dict[str, Any] | None]]] = {}
_ledger_repository: BaseResearchLedgerRepository | None = None
_ledger_tails: dict[str, asyncio.Task[None]] = {}
_ledger_errors: dict[str, Exception] = {}


def _owner_identity() -> tuple[str, str | None, str | None]:
    return (
        get_current_agent_id(),
        get_current_user_id(),
        get_current_session_id(),
    )


def _request_owner_identity(
    request: Request,
    *,
    session_id: str | None = None,
    user_id: str | None = None,
) -> tuple[str, str | None, str | None]:
    # ``user_id`` remains in the request schema for compatibility only.
    # Ownership always comes from authenticated server-side context.
    del user_id
    authenticated_user = getattr(request.state, "user", None)
    if isinstance(authenticated_user, dict):
        authenticated_user = (
            authenticated_user.get("username")
            or authenticated_user.get("id")
            or authenticated_user.get("sub")
        )
    resolved_user = (
        str(authenticated_user)
        if authenticated_user
        else get_current_user_id()
    )
    return (
        get_current_agent_id(),
        resolved_user,
        session_id or get_current_session_id(),
    )


def _dialog_owner(
    dialog: DialogRunState,
) -> tuple[str, str | None, str | None]:
    return (
        dialog.owner_agent_id,
        dialog.owner_user_id,
        dialog.owner_session_id,
    )


def _owned_dialog(
    plan_id: str,
    request: Request | None = None,
) -> DialogRunState:
    dialog = _dialog_runs.get(plan_id)
    if dialog is None:
        raise HTTPException(status_code=404, detail="Unknown dialog plan")
    current = (
        _request_owner_identity(request)
        if request is not None
        else _owner_identity()
    )
    owner = _dialog_owner(dialog)
    same_agent = owner[0] == current[0]
    same_user = owner[1] == current[1]
    if not same_agent or not same_user:
        raise HTTPException(status_code=404, detail="Unknown dialog plan")
    return dialog


def _plan_content_hash(plan_markdown: str) -> str:
    return hashlib.sha256(plan_markdown.encode("utf-8")).hexdigest()


def _build_plan_revision_proposal(
    dialog: DialogRunState,
    instruction: str = "",
) -> tuple[str, str]:
    plan = (dialog.plan_markdown or "").rstrip()
    requested = instruction.strip()
    if requested:
        section = (
            "## Requested Revision\n\n"
            f"{requested}\n\n"
            "Apply this request while preserving the existing acceptance, "
            "environment, and validation constraints."
        )
        return f"{plan}\n\n{section}\n", "根据聊天中的修改要求生成"

    approved_paths = _approved_plan_paths(plan)
    missing_paths = [
        path
        for path in dialog.unapproved_paths
        if path not in approved_paths
    ]
    if missing_paths:
        path_lines = "\n".join(
            f"- `{path.replace('`', '')}`" for path in missing_paths
        )
        section = (
            "## Proposed Scope Revision\n\n"
            "Add only the following observed changed paths to the approved "
            "scope:\n\n"
            f"{path_lines}\n\n"
            "Preserve all existing frozen paths and validation requirements."
        )
        return f"{plan}\n\n{section}\n", "根据未审批变更路径生成"

    feedback = (
        dialog.verification_summary
        or dialog.reproduction_summary
        or dialog.error
        or "Review the preserved validation report and address its findings."
    )
    section = (
        "## Proposed Validation Follow-up\n\n"
        f"- Address the preserved validation feedback: {feedback}\n"
        "- Keep the existing file scope unless the user explicitly approves "
        "a scope change.\n"
        "- Re-run the baseline reproduction and candidate verification."
    )
    return f"{plan}\n\n{section}\n", "根据最近一次验证反馈生成"


def _current_research_environment(
    host_platform: str | None = None,
    host_machine: str | None = None,
) -> tuple[str, str]:
    raw_platform = (host_platform or sys.platform).casefold()
    machine = (host_machine or platform.machine() or "unknown").strip()
    if raw_platform.startswith("win"):
        canonical = "windows"
        label = "Windows"
    elif raw_platform == "darwin":
        canonical = "macos"
        label = "macOS"
    elif raw_platform.startswith("linux"):
        canonical = "linux"
        label = "Linux"
    else:
        canonical = raw_platform or "unknown"
        label = host_platform or sys.platform or "unknown"
    return canonical, f"{label} ({machine})"


def _plan_environment_compatibility(
    plan_markdown: str,
    *,
    host_platform: str | None = None,
    host_machine: str | None = None,
) -> tuple[str, str, str]:
    current_platform, current_environment = _current_research_environment(
        host_platform,
        host_machine,
    )
    match = re.search(
        r"(?ims)^##\s+Reproduction Environment\s*$\s*(.*?)"
        r"(?=^##\s+|\Z)",
        plan_markdown,
    )
    if match is None:
        return (
            "unknown",
            current_environment,
            "计划未声明复现环境，请在批准前补充 Reproduction Environment。",
        )

    environment = match.group(1).casefold()
    if re.search(
        r"\b(?:cross[- ]platform|platform[- ]independent|any\s+os|all\s+platforms)\b",
        environment,
    ):
        return (
            "compatible",
            current_environment,
            f"计划声明为跨平台，可在当前 {current_environment} 环境验证。",
        )

    required: set[str] = set()
    if re.search(r"\bwindows\b|\bwin(?:dows)?\s*1[01]\b|\.exe\b", environment):
        required.add("windows")
    if re.search(r"\bmacos\b|\bmac\s*os\b|\bdarwin\b|\bos\s*x\b", environment):
        required.add("macos")
    if re.search(
        r"\blinux\b|\bubuntu\b|\bdebian\b|\bfedora\b|\bcentos\b",
        environment,
    ):
        required.add("linux")
    if not required:
        return (
            "unknown",
            current_environment,
            "计划中的复现环境无法自动判定，请人工核对后再批准。",
        )
    if current_platform in required:
        return (
            "compatible",
            current_environment,
            f"计划要求的复现平台包含当前 {current_environment}。",
        )

    labels = {"windows": "Windows", "macos": "macOS", "linux": "Linux"}
    required_label = "/".join(labels[item] for item in sorted(required))
    return (
        "incompatible",
        current_environment,
        f"计划要求 {required_label}，但当前执行环境是 {current_environment}；"
        "请选择能在当前环境复现和验证的问题。",
    )


def _dialog_payload(dialog: DialogRunState) -> dict[str, Any]:
    payload = asdict(dialog)
    for key in ("owner_agent_id", "owner_user_id", "owner_session_id"):
        payload.pop(key)
    return payload


def _recover_legacy_scope_failure(dialog: DialogRunState) -> DialogRunState:
    """Upgrade pre-recovery scope failures without discarding their worktree."""
    marker = "paths not listed in the approved plan:"
    if (
        dialog.status != "failed"
        or marker not in dialog.error
        or not dialog.worktree_path
    ):
        return dialog
    paths = [
        path.strip()
        for path in dialog.error.partition(marker)[2].split(",")
        if path.strip()
    ]
    timestamp = _utc_now()
    report = f"""# AutoResearch Validation Report

## Status

Recovered from a legacy scope-validation failure. No commit, push, or pull
request was created.

## Paths Outside Approved Plan

{chr(10).join(f"- `{path}`" for path in paths) or "- unavailable"}

## Validation Result

{dialog.error}

The existing worktree was preserved. Revise and reapprove the plan to continue.
"""
    return replace(
        dialog,
        status="needs_revision",
        unapproved_paths=paths,
        validation_failure_category="scope_mismatch",
        validation_report=dialog.validation_report or report,
        verification_status="blocked",
        verification_summary="Plan revision required.",
        validation_attempts=[
            *dialog.validation_attempts,
            {
                "attempt": len(dialog.validation_attempts) + 1,
                "revision": dialog.revision,
                "timestamp": timestamp,
                "failure_category": "scope_mismatch",
                "changed_paths": list(dialog.changed_paths),
                "unapproved_paths": paths,
                "reproduction_status": dialog.reproduction_status,
                "reproduction_summary": dialog.reproduction_summary,
                "verification_status": "blocked",
                "verification_summary": "Plan revision required.",
                "validation_report": dialog.validation_report or report,
            },
        ],
        updated_at=timestamp,
    )


def _run_owner(run: ResearchRunState) -> tuple[str, str | None, str | None]:
    return (run.owner_agent_id, run.owner_user_id, run.owner_session_id)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _datetime_iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


def _queue_ledger_write(
    run_id: str,
    operation: Callable[[BaseResearchLedgerRepository], Awaitable[Any]],
) -> None:
    repository = _ledger_repository
    if repository is None:
        return
    previous = _ledger_tails.get(run_id)

    async def ordered_write() -> None:
        if previous is not None:
            try:
                await previous
            except Exception:
                pass
        try:
            await operation(repository)
        except Exception as exc:
            _ledger_errors[run_id] = exc
            _log.exception("Research Ledger write failed for run %s", run_id)
            raise

    _ledger_tails[run_id] = asyncio.create_task(ordered_write())


async def _flush_ledger(run_id: str) -> None:
    tail = _ledger_tails.get(run_id)
    if tail is not None:
        try:
            await tail
        except Exception:
            pass
    error = _ledger_errors.pop(run_id, None)
    if error is not None:
        raise RuntimeError(
            f"Research Ledger write failed for {run_id}"
        ) from error


async def _persist_new_run(state: ResearchRunState, task_root: Path) -> None:
    repository = _ledger_repository
    if repository is None:
        return
    if await repository.get_task(state.task_id) is None:
        solution_path = next(task_root.glob("solution.*"))
        await repository.create_task(
            state.task_id,
            (task_root / "program.md").read_text(encoding="utf-8"),
            solution_path.name,
            solution_path.read_text(encoding="utf-8"),
            (task_root / "judge.py").read_text(encoding="utf-8"),
        )
    await repository.create_run(
        state.id,
        state.task_id,
        state.agent_id,
        state.rounds,
        owner_agent_id=state.owner_agent_id,
        owner_user_id=state.owner_user_id,
        owner_session_id=state.owner_session_id,
        research_brief=json.dumps(state.research_brief or {}),
    )
    await repository.update_run_status(
        state.id,
        state.status,
        state.phase,
        state.error,
    )
    for event in state.events:
        await repository.record_event(
            state.id,
            event.phase,
            event.round,
            event.detail,
            event.sequence,
        )


async def _restore_ledger_runs() -> None:
    repository = _ledger_repository
    if repository is None:
        return
    # ponytail: recent 500 is enough for UI history; paginate when history UI needs it.
    for stored in await repository.list_recent_runs(limit=500):
        events = await repository.list_events(stored.run_id)
        outcomes = await repository.list_outcomes(stored.run_id)
        run_events = tuple(
            ResearchRunEvent(
                event.phase,
                _datetime_iso(event.timestamp) or _utc_now(),
                event.round,
                event.detail,
                event.sequence,
            )
            for event in events
        )
        run_outcomes = tuple(
            ResearchOutcome(
                outcome.round,
                outcome.status,
                outcome.passed,
                outcome.baseline_score,
                outcome.candidate_score,
                outcome.improvement,
                json.loads(outcome.metrics or "{}"),
                outcome.error,
            )
            for outcome in outcomes
        )
        status = stored.status
        error = stored.error
        finished_at = _datetime_iso(stored.finished_at)
        if status in {"queued", "running"}:
            status = "failed"
            error = "Research run was interrupted by server restart"
            finished_at = _utc_now()
            sequence = run_events[-1].sequence + 1 if run_events else 0
            interrupted = ResearchRunEvent(
                "failed",
                finished_at,
                stored.current_round,
                error,
                sequence,
            )
            run_events = (*run_events, interrupted)
            await repository.record_event(
                stored.run_id,
                interrupted.phase,
                interrupted.round,
                interrupted.detail,
                interrupted.sequence,
            )
            await repository.finish_run(
                stored.run_id,
                status,
                "failed",
                error,
            )
        _runs[stored.run_id] = ResearchRunState(
            id=stored.run_id,
            task_id=stored.task_id,
            agent_id=stored.agent_id,
            owner_agent_id=stored.owner_agent_id,
            owner_user_id=stored.owner_user_id,
            owner_session_id=stored.owner_session_id,
            status=status,
            rounds=stored.rounds,
            completed_rounds=len(run_outcomes),
            outcomes=run_outcomes,
            created_at=_datetime_iso(stored.created_at) or _utc_now(),
            events=run_events,
            current_round=stored.current_round,
            phase="failed" if status == "failed" else stored.phase,
            updated_at=_datetime_iso(stored.updated_at) or _utc_now(),
            started_at=_datetime_iso(stored.started_at),
            finished_at=finished_at,
            error=error,
            research_brief=json.loads(stored.research_brief or "{}"),
        )


async def initialize_research_ledger(database_url: str | None = None) -> None:
    global _ledger_repository
    if _ledger_repository is not None:
        await close_research_ledger()
    if database_url is None:
        WORKING_DIR.mkdir(parents=True, exist_ok=True)
        database_url = (
            f"sqlite+aiosqlite:///{WORKING_DIR / 'research-ledger.db'}"
        )
    repository = PostgresResearchLedgerRepository(database_url)
    await repository.initialize()
    _ledger_repository = repository
    try:
        await _restore_ledger_runs()
    except Exception:
        await repository.close()
        _ledger_repository = None
        raise


async def close_research_ledger() -> None:
    global _ledger_repository
    runtime_tasks = tuple(
        {
            *(_runtime_tasks.values()),
            *(_dialog_tasks.values()),
        },
    )
    for task in runtime_tasks:
        task.cancel()
    if runtime_tasks:
        await asyncio.gather(*runtime_tasks, return_exceptions=True)
    _runtime_tasks.clear()
    _dialog_tasks.clear()
    _dialog_runtime_context.clear()
    repository = _ledger_repository
    if repository is None:
        return
    await asyncio.gather(*_ledger_tails.values(), return_exceptions=True)
    _ledger_tails.clear()
    _ledger_errors.clear()
    await repository.close()
    _ledger_repository = None


def get_research_root() -> Path:
    configured = os.environ.get("QWENPAW_RESEARCH_DIR")
    if configured:
        return Path(configured).expanduser().resolve()
    working_root = Path.cwd() / "research"
    if working_root.is_dir():
        return working_root.resolve()
    return (Path(__file__).resolve().parents[4] / "research").resolve()


def _get_task(task_id: str):
    if not _VALID_TASK_ID.fullmatch(task_id):
        raise HTTPException(status_code=404, detail="Research task not found")
    root = get_research_root().resolve()
    task_dir = (root / task_id).resolve()
    if task_dir.parent != root:
        raise HTTPException(status_code=404, detail="Research task not found")
    try:
        return load_task(task_dir)
    except ValueError as exc:
        raise HTTPException(
            status_code=404, detail="Research task not found"
        ) from exc


def _task_title(program: str, fallback: str) -> str:
    for line in program.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            return stripped.lstrip("#").strip() or fallback
    return fallback


def _active_run(
    task_id: str,
    owner: tuple[str, str | None, str | None] | None = None,
) -> ResearchRunState | None:
    return next(
        (
            run
            for run in _runs.values()
            if run.task_id == task_id
            and run.status in {"queued", "running"}
            and (owner is None or _run_owner(run) == owner)
        ),
        None,
    )


def _latest_run(
    task_id: str,
    owner: tuple[str, str | None, str | None] | None = None,
) -> ResearchRunState | None:
    return max(
        (
            run
            for run in _runs.values()
            if run.task_id == task_id
            and (owner is None or _run_owner(run) == owner)
        ),
        key=lambda run: run.created_at,
        default=None,
    )


def _owned_run(run_id: str) -> ResearchRunState:
    run = _runs.get(run_id)
    if run is None or _run_owner(run) != _owner_identity():
        raise HTTPException(status_code=404, detail="Research run not found")
    return run


def _run_payload(run: ResearchRunState) -> dict[str, Any]:
    payload = asdict(run)
    for key in ("owner_agent_id", "owner_user_id", "owner_session_id"):
        payload.pop(key)
    return payload


def _report_event(
    run_id: str,
    phase: str,
    round_number: int | None = None,
    detail: str = "",
) -> None:
    current = _runs.get(run_id)
    if current is None:
        return
    now = _utc_now()
    seq = len(current.events)
    event = ResearchRunEvent(phase, now, round_number, detail, seq)
    _runs[run_id] = replace(
        current,
        events=(*current.events, event),
        current_round=(
            round_number if round_number is not None else current.current_round
        ),
        phase=phase,
        updated_at=now,
    )
    persisted = _runs[run_id]

    async def persist(repository: BaseResearchLedgerRepository) -> None:
        await repository.record_event(
            run_id,
            event.phase,
            event.round,
            event.detail,
            event.sequence,
        )
        await repository.update_run_status(
            run_id,
            persisted.status,
            persisted.phase,
            persisted.error,
        )
        await repository.update_run_progress(
            run_id,
            persisted.completed_rounds,
            persisted.current_round,
        )

    _queue_ledger_write(run_id, persist)


def _summary(task_id: str) -> dict[str, Any]:
    task = _get_task(task_id)
    program = task.program.read_text(encoding="utf-8")
    owner = _owner_identity()
    active = _active_run(task_id, owner)
    latest = _latest_run(task_id, owner)
    return {
        "id": task_id,
        "title": _task_title(program, task_id),
        "solution_name": task.solution.name,
        "active_run_id": active.id if active else None,
        "latest_run_id": latest.id if latest else None,
    }


@router.get("/tasks")
async def list_tasks() -> list[dict[str, Any]]:
    root = get_research_root()
    if not root.is_dir():
        return []
    result: list[dict[str, Any]] = []
    for path in sorted(root.iterdir()):
        if not path.is_dir() or not _VALID_TASK_ID.fullmatch(path.name):
            continue
        try:
            result.append(_summary(path.name))
        except HTTPException:
            continue
    return result


@router.get("/tasks/{task_id}")
async def get_task(task_id: str) -> dict[str, Any]:
    task = _get_task(task_id)
    program = await asyncio.to_thread(task.program.read_text, encoding="utf-8")
    solution = await asyncio.to_thread(
        task.solution.read_text, encoding="utf-8"
    )
    if unsafe_research_enabled():
        evaluation = await asyncio.to_thread(
            evaluate_candidate, task, solution
        )
        evaluation_payload = asdict(evaluation)
        if not math.isfinite(evaluation.score):
            evaluation_payload["score"] = None
    else:
        evaluation_payload = {
            "passed": False,
            "score": None,
            "metrics": {},
            "error": (
                "Evaluation disabled because best-effort isolation is not a "
                "complete security boundary; set "
                f"{UNSAFE_RESEARCH_OPT_IN} to opt in locally"
            ),
        }
    history = await asyncio.to_thread(list_research_history, task)
    return {
        **_summary(task_id),
        "program": program,
        "solution": solution,
        "evaluation": evaluation_payload,
        "history": history,
    }


@router.post("/tasks", status_code=201)
async def create_task(body: CreateResearchTask) -> dict[str, Any]:
    """Create a new research task via dialog (替代三文件契约)."""
    if not unsafe_research_enabled():
        raise HTTPException(
            status_code=503,
            detail=(
                "Research evaluator uses best-effort isolation rather than a "
                "complete security boundary; set "
                f"{UNSAFE_RESEARCH_OPT_IN} to opt in locally"
            ),
        )
    root = get_research_root()
    try:
        task = await asyncio.to_thread(
            create_research_task,
            root,
            body.task_id,
            body.program,
            body.solution_name,
            body.solution_source,
            body.judge_source,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _summary(body.task_id)


def _report_outcome(run_id: str, outcome: ResearchOutcome) -> None:
    current = _runs.get(run_id)
    if current is None:
        return
    outcomes = (*current.outcomes, outcome)
    _runs[run_id] = replace(
        current,
        outcomes=outcomes,
        completed_rounds=len(outcomes),
    )

    async def persist(repository: BaseResearchLedgerRepository) -> None:
        await repository.record_outcome(
            run_id,
            outcome.round,
            outcome.status,
            outcome.passed,
            outcome.baseline_score,
            outcome.candidate_score,
            outcome.improvement,
            json.dumps(dict(outcome.metrics)),
            outcome.error,
            "",
            "",
        )

    _queue_ledger_write(run_id, persist)
    _report_event(run_id, outcome.status, outcome.round, outcome.error)


def _report_progress(run_id: str, progress: tuple[int, str, str]) -> None:
    round_number, phase, detail = progress
    _report_event(run_id, phase, round_number, detail)


def _finish_run(
    run_id: str,
    status: str,
    error: str = "",
    *,
    allow_overwrite: bool = False,
) -> None:
    """Safely transition a run to a terminal status.

    Only transitions from non-terminal states (queued, running), or when
    explicitly told to overwrite (e.g. cancel overrides completed).
    """
    existing = _runs.get(run_id)
    if existing is None:
        return
    if (
        existing.status
        in {"completed", "failed", "cancelled", "auth_required"}
        and not allow_overwrite
    ):
        _log.warning(
            "_finish_run: refusing to transition run %s from %s to %s",
            run_id,
            existing.status,
            status,
        )
        return
    _runs[run_id] = replace(
        existing,
        status=status,
        finished_at=_utc_now(),
        error=error or existing.error,
        updated_at=_utc_now(),
    )
    # Record terminal event in the run's event log
    _report_event(run_id, status, detail=error)

    async def persist(repository: BaseResearchLedgerRepository) -> None:
        await repository.finish_run(run_id, status, status, error)

    _queue_ledger_write(run_id, persist)
    # Send terminal SSE events for frontend consumption
    _sse_broadcast(
        run_id,
        {
            "type": f"run.{status}",
            "run_id": run_id,
            "status": status,
            "error": error,
        },
    )
    _sse_close(run_id)


async def _execute_run(
    run_id: str,
    task_dir: Path,
    body: StartResearchRun,
) -> None:
    current = _runs[run_id]
    started_at = _utc_now()
    _runs[run_id] = replace(
        current,
        status="running",
        started_at=started_at,
        updated_at=started_at,
    )
    _report_event(run_id, "starting")
    try:
        outcomes = await _run_with_qwenpaw(
            task_dir,
            body.model,
            body.rounds,
            3,
            300,
            10.0,
            0.0,
            None,
            current.agent_id,
            on_outcome=lambda outcome: _report_outcome(run_id, outcome),
            on_progress=lambda progress: _report_progress(run_id, progress),
            is_cancelled=lambda: (
                (state := _runs.get(run_id)) is None
                or state.status == "cancelled"
            ),
        )
        fatal = next(
            (
                outcome
                for outcome in outcomes
                if outcome.status in FATAL_OUTCOME_STATUSES
            ),
            None,
        )
        if fatal is not None:
            if fatal.status == "cancelled":
                _finish_run(
                    run_id,
                    "cancelled",
                    error=fatal.error or "Research run was cancelled",
                )
            else:
                _finish_run(
                    run_id,
                    "failed",
                    error=fatal.error or f"Research failed: {fatal.status}",
                )
            return

        # Finalize optional PR before publishing the completed terminal event.
        run_state = _runs.get(run_id)
        if run_state is not None:
            await _try_create_pr(run_id, run_state.task_id)
        _finish_run(run_id, "completed")
    except asyncio.CancelledError:
        if _runs[run_id].status != "cancelled":
            _finish_run(
                run_id,
                "cancelled",
                error="Research run was cancelled",
                allow_overwrite=True,
            )
    except Exception as exc:
        _finish_run(run_id, "failed", error=f"{type(exc).__name__}: {exc}")
    finally:
        _runtime_tasks.pop(run_id, None)
        try:
            await _flush_ledger(run_id)
        except RuntimeError:
            _log.exception("Research Ledger flush failed for run %s", run_id)


@router.post("/tasks/{task_id}/runs", status_code=202)
async def start_run(task_id: str, body: StartResearchRun) -> dict[str, Any]:
    if not unsafe_research_enabled():
        raise HTTPException(
            status_code=503,
            detail=(
                "Research evaluator uses best-effort isolation rather than a "
                "complete security boundary; set "
                f"{UNSAFE_RESEARCH_OPT_IN} to opt in locally"
            ),
        )
    task = _get_task(task_id)
    owner_agent_id, owner_user_id, owner_session_id = _owner_identity()
    if body.agent_id != owner_agent_id:
        raise HTTPException(
            status_code=403,
            detail="Research runs must use the current agent",
        )
    # ponytail: task solution is shared, so runs serialize globally per task.
    if _active_run(task_id) is not None:
        raise HTTPException(
            status_code=409, detail="This task is already running"
        )
    run_id = uuid.uuid4().hex
    created_at = _utc_now()
    queued = ResearchRunEvent("queued", created_at)
    state = ResearchRunState(
        id=run_id,
        task_id=task_id,
        agent_id=owner_agent_id,
        owner_agent_id=owner_agent_id,
        owner_user_id=owner_user_id,
        owner_session_id=owner_session_id,
        status="queued",
        rounds=body.rounds,
        completed_rounds=0,
        outcomes=(),
        created_at=created_at,
        events=(queued,),
        current_round=None,
        phase="queued",
        updated_at=created_at,
    )
    _runs[run_id] = state
    await _persist_new_run(state, task.root)
    _runtime_tasks[run_id] = asyncio.create_task(
        _execute_run(run_id, task.root, body)
    )
    return _run_payload(state)


@router.get("/runs/{run_id}")
async def get_run(run_id: str) -> dict[str, Any]:
    return _run_payload(_owned_run(run_id))


@router.post("/runs/{run_id}/cancel", status_code=202)
async def cancel_run(run_id: str) -> dict[str, Any]:
    run = _owned_run(run_id)
    if run.status not in {"queued", "running"}:
        raise HTTPException(
            status_code=409, detail="Research run is not active"
        )
    _finish_run(
        run_id,
        "cancelled",
        error="Research run was cancelled",
        allow_overwrite=True,
    )
    task = _runtime_tasks.pop(run_id, None)
    if task is not None:
        task.cancel()
    try:
        await _flush_ledger(run_id)
    except RuntimeError:
        _log.exception("Research Ledger flush failed for run %s", run_id)
    return _run_payload(_runs[run_id])


def _reset_runs_for_tests() -> None:
    for task in tuple(_runtime_tasks.values()):
        task.cancel()
    _runtime_tasks.clear()
    _runs.clear()
    _run_auto_pr.clear()


# ── PR submission helpers ──────────────────────────────────────────────────


def _build_pr_body(program: str, task_id: str, solution: str) -> str:
    short = program[:1500] + ("..." if len(program) > 1500 else "")
    return (
        f"## 🤖 AutoResearch Fix\n\n"
        f"This PR was automatically created by QwenPaw's auto-research engine "
        f"for task `{task_id}`.\n\n"
        f"### Problem\n{short}\n\n"
        f"### Changes\nThe solution was iteratively improved through the research engine.\n"
        f"See `research/{task_id}/` for the full research history.\n"
    )


async def _try_create_pr(run_id: str, task_id: str) -> None:
    """Best-effort GitHub PR creation after successful research."""
    if not _run_auto_pr.pop(run_id, False):
        return
    try:
        task = _get_task(task_id)
    except HTTPException:
        return

    _report_event(run_id, "creating_pr")

    try:
        program = task.program.read_text(encoding="utf-8")
        title = _task_title(program, task_id)
        solution = task.solution.read_text(encoding="utf-8")

        # Find the nearest git repo (project root, not research/ dir)
        root = get_research_root()
        repo_dir = str(root)
        for candidate in (root.parent, root.parent.parent):
            if (candidate / ".git").is_dir():
                repo_dir = str(candidate)
                break

        proc = await asyncio.create_subprocess_exec(
            "gh",
            "pr",
            "create",
            "--title",
            f"[AutoResearch] {title}",
            "--body",
            _build_pr_body(program, task_id, solution),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=repo_dir,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=60)

        if proc.returncode == 0:
            pr_url = stdout.decode().strip()
            _report_event(run_id, "pr_created", detail=pr_url)
            _runs[run_id] = replace(
                _runs[run_id],
                submission=SubmissionResult("pr_created", pr_url, pr_url),
            )
        else:
            err = (stderr.decode() + stdout.decode()).strip()
            _report_event(run_id, "pr_failed", detail=err[:500])
    except Exception as exc:
        _report_event(
            run_id, "pr_failed", detail=f"{type(exc).__name__}: {exc}"
        )


# ── Dialog-based research: plan → create → run → PR ───────────────────────


async def _fetch_github_issue_evidence(goal: str) -> str:
    match = _GITHUB_REPOSITORY_RE.search(goal)
    if match is None:
        return ""
    owner, repository = match.groups()
    url = f"https://api.github.com/repos/{owner}/{repository}/issues"
    try:
        import httpx

        async with httpx.AsyncClient(
            timeout=30,
            follow_redirects=False,
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": "QwenPaw-AutoResearch",
            },
        ) as client:
            response = await client.get(
                url,
                params={"state": "open", "per_page": 30, "sort": "updated"},
            )
            response.raise_for_status()
            rows = response.json()
    except Exception as exc:
        _log.warning("GitHub issue discovery failed for %s: %s", url, exc)
        return f"GitHub Issues API unavailable: {type(exc).__name__}: {exc}"

    issues: list[str] = []
    for row in rows:
        if not isinstance(row, dict) or "pull_request" in row:
            continue
        labels = ", ".join(
            str(label.get("name", ""))
            for label in row.get("labels", [])
            if isinstance(label, dict)
        )
        assignee = row.get("assignee")
        assigned = (
            str(assignee.get("login", ""))
            if isinstance(assignee, dict)
            else "unassigned"
        )
        body = re.sub(r"\s+", " ", str(row.get("body") or "")).strip()[:1200]
        issues.append(
            "\n".join(
                [
                    f"#{row.get('number')}: {row.get('title', '')}",
                    f"labels={labels or 'none'}; assignee={assigned}",
                    f"url={row.get('html_url', '')}",
                    f"body={body or '(empty)'}",
                ],
            ),
        )
    return "\n\n".join(issues[:20])


def _build_discovery_prompt(
    goal: str,
    rounds: int,
    issue_evidence: str = "",
) -> str:
    _, current_environment = _current_research_environment()
    return (
        "Research the codebase before proposing any code changes.\n"
        "Use the supplied issue evidence to identify likely root causes and "
        "rank solution directions. This is a triage phase, not implementation.\n"
        "Do not call repository tools in this phase. The following planning "
        "phase will inspect source files and tests after a direction is chosen.\n"
        "Return the JSON object immediately after reviewing the supplied "
        "evidence; do not keep searching for completeness.\n"
        "For GitHub issue work, rank only the real open issues in the supplied "
        "GitHub evidence. Prefer an unassigned, narrowly scoped, testable issue "
        "and include its issue number in every candidate title.\n"
        f"Current execution environment: {current_environment}.\n"
        "Only rank issues whose reported behavior can be reproduced and whose "
        "fix can be verified in this environment. Exclude Windows-only issues "
        "on macOS or Linux, macOS-only issues on Windows or Linux, and Linux-only "
        "issues on Windows or macOS. Also exclude installer, driver, hardware, "
        "or packaging issues unless the exact affected platform or artifact is "
        "available in the current environment.\n"
        "Do not generate implementation code.\n\n"
        "Return only one JSON object with this exact shape:\n"
        "{\n"
        f'  "goal": {json.dumps(goal)},\n'
        '  "current_behavior": "...",\n'
        '  "root_cause_hypotheses": ["..."],\n'
        '  "candidate_directions": [\n'
        '    {"id": "short-id", "title": "...", "priority": 1, '
        '"risk": "low|medium|high", "reason": "..."}\n'
        "  ],\n"
        '  "success_metrics": ["..."],\n'
        f'  "iteration_budget": {rounds},\n'
        '  "modifiable_files": ["path"],\n'
        '  "relevant_tests": ["path"]\n'
        "}\n\n"
        f"GitHub issue evidence:\n{issue_evidence or '(not applicable)'}\n"
    )


def _parse_research_brief(
    response: str,
    *,
    goal: str,
    rounds: int,
) -> ResearchBrief:
    start = response.find("{")
    end = response.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("Discovery response does not contain a JSON object")
    try:
        payload = json.loads(response[start : end + 1])
    except json.JSONDecodeError as exc:
        raise ValueError(f"Discovery JSON is invalid: {exc}") from exc
    payload["goal"] = goal
    payload["iteration_budget"] = rounds
    try:
        return ResearchBrief.model_validate(payload)
    except Exception as exc:
        raise ValueError(f"ResearchBrief validation failed: {exc}") from exc


def _build_planning_prompt(goal: str) -> str:
    """Original monolithic prompt — kept for backward compatibility."""
    return _build_plan_only_prompt(goal)


def _build_plan_only_prompt(
    goal: str,
    brief: ResearchBrief | None = None,
    issue_evidence: str = "",
) -> str:
    """Phase 1: Generate program.md — research codebase, describe problem."""
    _, current_environment = _current_research_environment()
    return (
        "You are a research task planner for the QwenPaw auto-research system.\n"
        "Your job: Produce a structured program.md file from the discovery brief.\n"
        "\n"
        "Steps:\n"
        "1. Analyze the goal to understand the problem.\n"
        "2. If the goal mentions GitHub issues, use the supplied GitHub evidence.\n"
        "3. Search the codebase with read_file, grep_search, or glob_search.\n"
        "4. Read the target source file(s) to understand current behavior.\n"
        "5. Write a detailed program.md with problem description, acceptance criteria,\n"
        "   relevant file paths, code snippets, and suggested test cases.\n"
        f"6. Confirm the issue is reproducible on {current_environment}. If its "
        "reported environment requires another OS, installer, driver, hardware, "
        "or unavailable artifact, select a different candidate from the brief.\n"
        "\n"
        "STOP after 5 tool calls — produce the output below.\n"
        "\n"
        "OUTPUT FORMAT — your ENTIRE response must be:\n"
        "\n"
        "TASK_ID: <kebab-case-id>\n"
        "<<<FILE:program.md>>>\n"
        "# Problem Title\n"
        "\n"
        "Repository: https://github.com/<owner>/<repo>\n"
        "Issue: #<number>\n"
        "\n"
        "## Goal\n"
        "Description of what needs to be fixed/improved\n"
        "\n"
        "## Acceptance Criteria\n"
        "- Criterion 1\n"
        "- Criterion 2\n"
        "\n"
        "## Modifiable Files\n"
        "- src/path/to/file.py\n"
        "- tests/path/to/test_file.py\n"
        "\n"
        "## Frozen Files\n"
        "- src/path/to/other.py\n"
        "\n"
        "## Run\n"
        "python src/path/to/file.py\n"
        "\n"
        "## Metrics\n"
        "- metric_name\n"
        "\n"
        "## Iterations: 5\n"
        "\n"
        "## Current Behavior\n"
        "What the current code does and why it's wrong\n"
        "\n"
        "## Reproduction Environment\n"
        "Version: <affected release or commit>\n"
        "OS: <reported operating system and architecture>\n"
        "Install: <reported installation method>\n"
        "Runtime: <reported Python, Node.js, or bundled runtime version>\n"
        "\n"
        "## Reproduction\n"
        "Baseline Ref: <tag, branch, or commit that contains the bug>\n"
        "Steps:\n"
        "1. <issue reproduction step>\n"
        "Expected Failure: <observable behavior that proves the issue>\n"
        "\n"
        "## Verification\n"
        "Expected Pass: <observable behavior after the fix>\n"
        "<<<END>>>\n"
        "\n"
        "⚠️  The <<<FILE:program.md>>> / <<<END>>> delimiters are MANDATORY.\n"
        "⚠️  TASK_ID must be on its own line BEFORE the file block.\n"
        "⚠️  Every file the implementation may change, including tests, must be "
        "listed exactly under Modifiable Files.\n"
        "⚠️  Do NOT output judge.py or solution.py — only program.md.\n"
        "\n"
        f"User goal: {goal}\n"
        f"Discovery brief:\n{brief.model_dump_json(indent=2) if brief else '{}'}\n"
        f"GitHub issue evidence:\n{issue_evidence or '(not applicable)'}"
    )


def _build_solution_prompt(goal: str, program_md: str) -> str:
    """Phase 2: Extract the current buggy code from codebase."""
    return (
        "You are extracting the current buggy source code for a research task.\n"
        "\n"
        "The research planner has written the following problem description:\n"
        "\n"
        f"--- program.md ---\n{program_md}\n--- end program.md ---\n"
        "\n"
        "Your task: Read the source files mentioned in program.md from the codebase,\n"
        "and output the current (buggy) version as solution.py.\n"
        "\n"
        "OUTPUT FORMAT — your ENTIRE response must be:\n"
        "\n"
        "<<<FILE:solution.py>>>\n"
        "# [Copy-paste the current code from the codebase here — don't modify it]\n"
        "<<<END>>>\n"
        "\n"
        "⚠️  Do NOT fix the bugs yet — copy the code AS-IS.\n"
        "⚠️  The <<<FILE:solution.py>>> / <<<END>>> delimiters are MANDATORY.\n"
        "⚠️  Do NOT output program.md or judge.py — only solution.py.\n"
        "\n"
        f"User goal: {goal}"
    )


def _build_judge_prompt(goal: str, program_md: str, solution_code: str) -> str:
    """Phase 3: Generate judge.py that evaluates solutions."""
    return (
        "You are writing a judge (evaluator) for a research task.\n"
        "\n"
        "The problem description:\n"
        f"--- program.md ---\n{program_md[:2000]}\n--- end ---\n"
        "\n"
        "The current buggy code:\n"
        f"--- solution.py ---\n{solution_code[:2000]}\n--- end ---\n"
        "\n"
        "Write a judge.py that evaluates candidate solutions.\n"
        "It runs as: python3 judge.py <solution_path>\n"
        "\n"
        "OUTPUT FORMAT — your ENTIRE response must be:\n"
        "\n"
        "<<<FILE:judge.py>>>\n"
        "import json, sys, ast\n"
        "\n"
        'with open(sys.argv[1], encoding="utf-8") as f:\n'
        "    source = f.read()\n"
        "\n"
        "# CRITICAL — sentinel check (MUST come first):\n"
        "if source.strip() == '__QWENPAW_INVALID_CANDIDATE__':\n"
        '    print(json.dumps({"passed": False, "score": 0, "metrics": {}, "error": "invalid candidate"}))\n'
        "    sys.exit(0)\n"
        "\n"
        "# Validate syntax:\n"
        "try:\n"
        "    ast.parse(source)\n"
        "except SyntaxError as e:\n"
        '    print(json.dumps({"passed": False, "score": 0, "metrics": {}, "error": str(e)}))\n'
        "    sys.exit(0)\n"
        "\n"
        "# Your test logic here — run the solution, check outputs, compute score\n"
        "# ...\n"
        "\n"
        "score = 0  # compute from test results\n"
        'print(json.dumps({"passed": True, "score": score, "metrics": {}, "error": ""}))\n'
        "<<<END>>>\n"
        "\n"
        "REQUIREMENTS:\n"
        "1. Sentinel check MUST be first — reject '__QWENPAW_INVALID_CANDIDATE__'\n"
        "2. ast.parse() MUST be called — reject SyntaxError\n"
        "3. MUST actually read and test the solution (NOT a no-op judge)\n"
        "4. Output ONLY valid JSON on stdout\n"
        "5. Use sys.stderr.write() for any debug logs\n"
        "\n"
        "SCORING:\n"
        "- Negative score = fails test cases\n"
        "- Zero = compiles but doesn't solve problem\n"
        "- Positive = passes test cases (more tests = higher)\n"
        "\n"
        "⚠️  The <<<FILE:judge.py>>> / <<<END>>> delimiters are MANDATORY.\n"
        "⚠️  Do NOT output program.md or solution.py — only judge.py.\n"
        "\n"
        f"User goal: {goal}"
    )


def _generate_fallback_task_id(goal: str) -> str:
    """Generate a kebab-case task_id from the goal text as fallback."""
    import re as _re

    # Take first ~60 chars, lower, replace non-alnum with hyphens, collapse
    slug = _re.sub(r"[^a-z0-9]+", "-", goal.lower().strip())[:60].strip("-")
    if not slug:
        slug = "research-task"
    # Ensure starts with alnum
    slug = _re.sub(r"^[^a-z0-9]+", "", slug)
    if not slug:
        slug = "research-task"
    return slug


def _format_plan_error(raw: str, response_snippet: str | None = None) -> str:
    """Convert raw LLM/API error into a user-friendly message.

    Args:
        raw: Raw error string from the agent
        response_snippet: Optional snippet of the actual LLM response for debugging
    """
    import re as _re

    # Short / truncated response — model didn't generate proper output
    if response_snippet and len(response_snippet) < 200:
        return (
            f"⚠️ 模型返回了异常短的响应（{len(response_snippet)} 字符），"
            "可能原因：\n"
            "  1. API 配额或速率限制\n"
            "  2. 模型不理解或拒绝处理该目标\n"
            "  3. 搜索/工具调用全部失败\n"
            f"💡 响应内容: {response_snippet[:300]}\n"
            "💡 建议：切换模型、简化目标描述、或在「设置」中检查 API 密钥。"
        )

    # Rate limit errors (429) from OpenRouter / various providers
    if "429" in raw or "rate limit" in raw.lower() or "rate_limit" in raw:
        # Try to extract the provider model name
        model_match = _re.search(r"'model'\s*:\s*'([^']+)'", raw)
        model_name = model_match.group(1) if model_match else "当前模型"

        # Extract remaining/reset info
        remaining = _re.search(
            r"X-RateLimit-Remaining['\"]?\s*:\s*['\"]?(\d+)", raw
        )
        if remaining and remaining.group(1) == "0":
            return (
                f"⏳ {model_name} 今日免费额度已用完。\n"
                "💡 解决方法：\n"
                "  1. 切换模型 — 在下拉菜单中选择其他免费模型\n"
                "  2. 等待明天重置（UTC 00:00）\n"
                "  3. 在 OpenRouter 添加 credits 解锁更多请求"
            )
        return (
            f"⏳ API 频率限制：{model_name} 请求过于频繁，请稍后重试。\n"
            "💡 可尝试切换其他模型或等待几秒后重试。"
        )

    # Timeout errors
    if "timeout" in raw.lower() or "deadline" in raw.lower():
        return (
            "⏱️ 模型响应超时。\n"
            "💡 可尝试：减少轮数、简化需求描述、或切换更快的模型。"
        )

    # Connection errors
    if "connection" in raw.lower() or "disconnected" in raw.lower():
        return "🔌 模型连接中断，请稍后重试。\n" "💡 可尝试切换其他模型。"

    # Generic: truncate very long raw errors
    if len(raw) > 300:
        return f"规划失败：{raw[:300]}..."
    return f"规划失败：{raw}"


def _parse_planning_output(response: str, goal: str = "") -> dict[str, str]:
    """Parse agent response to extract task_id + 3 file contents.

    Supports three formats (tried in order):
    1. ``<<<FILE:name>>>...<<<END>>>`` markers (primary)
    2. ````program.md```` fenced code blocks (fallback)
    3. JSON ``{"files": [{"path": "...", "content": "..."}]}`` (fallback)
    """
    # Extract task ID — try TASK_ID: line first, fall back to auto-generate
    task_id_match = re.search(
        r"TASK_ID:\s*([A-Za-z0-9][A-Za-z0-9._-]*)", response
    )
    if not task_id_match:
        task_id = _generate_fallback_task_id(goal)
        _log.warning(
            "LLM response missing TASK_ID line — auto-generated '%s' from goal",
            task_id,
        )
    else:
        task_id = task_id_match.group(1)

    # ── Format 1: <<<FILE:name>>>...<<<END>>> markers ──
    files: dict[str, str] = {}
    for match in _PLANNING_TAG_RE.finditer(response):
        filename = match.group(1).strip()
        content = match.group(2).strip()
        files[filename] = content

    # ── Format 2: ```<filename> fenced code blocks ──
    if not files:
        _MARKDOWN_TAG_RE = re.compile(
            r"```(program\.md|solution\.[a-z]+|judge\.py)\s*\n(.*?)```",
            re.DOTALL | re.IGNORECASE,
        )
        for match in _MARKDOWN_TAG_RE.finditer(response):
            filename = match.group(1).strip()
            content = match.group(2).strip()
            files[filename] = content

    # ── Format 3: JSON structured output ──
    if not files:
        try:
            import json as _json

            _JSON_BLOCK_RE = re.compile(
                r'\{[^{}]*"files"\s*:\s*\[.*?\][^{}]*\}',
                re.DOTALL,
            )
            json_match = _JSON_BLOCK_RE.search(response)
            if json_match:
                data = _json.loads(json_match.group())
                for f in data.get("files", []):
                    path = f.get("path", "").strip()
                    content = f.get("content", "").strip()
                    if path and content:
                        files[path] = content
                if files:
                    _log.info(
                        "Parsed JSON format, got files: %s", list(files.keys())
                    )
        except Exception:
            pass  # JSON parsing is best-effort

    # ── Validation ──
    required = {"program.md", "judge.py"}
    missing = required - set(files.keys())
    if missing:
        found_files = list(files.keys()) or ["(none)"]
        hint = (
            f"缺少文件: {', '.join(sorted(missing))}。"
            f"找到的文件: {', '.join(found_files)}。"
            f"LLM 输出长度: {len(response)} 字符。"
        )
        if len(response) < 500 and not any(
            k in response
            for k in ("program.md", "judge.py", "FILE:", '"files"')
        ):
            hint += (
                " 响应中未找到任何文件标记。"
                "请确保使用了正确的响应格式: <<<FILE:program.md>>> ... <<<END>>>"
            )
        raise ValueError(hint)

    # Find solution file
    solution_files = {
        k: v
        for k, v in files.items()
        if k.startswith("solution.") and k != "program.md"
    }
    if len(solution_files) != 1:
        raise ValueError(
            f"Planning response must have exactly one solution.* file, "
            f"got: {list(solution_files)}"
        )
    solution_name, solution_content = next(iter(solution_files.items()))

    # ── Strict content validation ──
    program = files["program.md"]
    judge = files["judge.py"]

    if len(program.strip()) < 20:
        raise ValueError(
            f"program.md 内容过短（{len(program)} 字符），至少需要 20 字符"
        )
    if len(judge.strip()) < 50:
        raise ValueError(
            f"judge.py 内容过短（{len(judge)} 字符），至少需要 50 字符"
        )
    if len(solution_content.strip()) < 10:
        raise ValueError(
            f"{solution_name} 内容过短（{len(solution_content)} 字符），至少需要 10 字符"
        )

    # Validate judge.py syntax
    try:
        import ast as _ast

        _ast.parse(judge)
    except SyntaxError as e:
        raise ValueError(
            f"judge.py 存在 Python 语法错误: {e}\n"
            f"前 200 字符: {judge[:200]}"
        ) from e

    # Check judge.py has essential elements
    _judge_lower = judge.lower()
    _required_judge_elements = [
        ("sys.argv", "缺少命令行参数读取 (sys.argv)"),
        ("json.dumps", "缺少 JSON 输出 (json.dumps)"),
        (
            "__qwenpaw_invalid_candidate__",
            "缺少哨兵值检查 (__QWENPAW_INVALID_CANDIDATE__)",
        ),
    ]
    _judge_warnings = [
        msg
        for keyword, msg in _required_judge_elements
        if keyword not in _judge_lower
    ]
    if _judge_warnings:
        _log.warning("judge.py validation warnings: %s", _judge_warnings)

    return {
        "task_id": task_id,
        "program": program,
        "solution_name": solution_name,
        "solution_source": solution_content,
        "judge_source": judge,
    }


def _dialog_broadcast(plan_id: str, event: dict[str, Any]) -> None:
    queues = _dialog_sse_queues.get(plan_id)
    if queues:
        dead: list[asyncio.Queue[dict[str, Any] | None]] = []
        for q in queues:
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                dead.append(q)
        for q in dead:
            queues.discard(q)


def _dialog_close(plan_id: str) -> None:
    queues = _dialog_sse_queues.pop(plan_id, None)
    if queues:
        for q in queues:
            try:
                q.put_nowait(None)
            except asyncio.QueueFull:
                pass


def _dialog_emit(plan_id: str, phase: str, detail: str = "") -> None:
    event = {"type": "event", "phase": phase, "detail": detail}
    _dialog_broadcast(plan_id, event)
    ds = _dialog_runs.get(plan_id)
    if ds:
        ds.events.append(
            {"phase": phase, "detail": detail, "timestamp": _utc_now()}
        )
        ds.updated_at = _utc_now()


# ── P1: Split-phase planning helpers ───────────────────────────────────────

_MAX_ATTEMPTS = 2  # per-phase max attempts (including first try)

# Per-phase output size limits (characters). These prevent runaway token usage
# while giving each phase enough room. program.md gets the most because it
# includes research notes from codebase exploration.
_PHASE_OUTPUT_LIMITS: dict[str, tuple[int, int]] = {
    # (min_chars, max_chars)
    "research_brief": (100, 12_000),
    "program.md": (50, 16_000),
    "solution.py": (20, 32_000),
    "judge.py": (50, 16_000),
}

_PHASE_FAILURE_MARKERS = (
    "executed maximum iterations of reasoning-acting loop",
)

# Discovery is intentionally shorter than implementation-oriented phases.
# It receives no tools and only synthesizes the already-fetched issue evidence.
_DISCOVERY_MAX_ITERS = 3

_IGNORED_RESEARCH_RUNTIME_PATHS = frozenset(
    {
        ".skill.json.lock",
        "skill.json",
    }
)


def _derive_task_id(goal: str) -> str:
    """Derive a kebab-case task ID from the goal string."""
    import re as _re

    # Take first 5 words, lowercase, replace non-alnum with dash
    words = goal.strip().lower().split()[:6]
    slug = "-".join(
        _re.sub(r"[^a-z0-9-]", "", w)
        for w in words
        if _re.sub(r"[^a-z0-9-]", "", w)
    )
    return slug[:60] or "auto-task"


async def _run_single_phase(
    *,
    plan_id: str,
    instruction: str,
    agent_config: object,
    request_context: dict,
    max_iters: int,
    timeout: int,
    phase_label: str,
    require_tools: bool = False,
    allowed_tools: list[str] | None = None,
) -> str | None:
    """Run one phase of the split plan pipeline with retries.

    Returns the raw response text on success, or None (after emitting error) on failure.
    """
    from ...config.config import load_agent_config

    ds = _dialog_runs.get(plan_id)
    runtime_context = _dialog_runtime_context.get(plan_id, {})
    phase_max_iters = (
        min(max_iters, _DISCOVERY_MAX_ITERS)
        if phase_label == "research_brief"
        else max_iters
    )
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        result = await _run_task(
            instruction=instruction,
            agent_config=agent_config,
            request_context={
                **request_context,
                "session_id": f"{request_context['session_id']}-r{attempt}",
            },
            max_iters=phase_max_iters,
            timeout=timeout,
            output_dir=None,
            require_tools=require_tools,
            workspace=runtime_context.get("workspace"),
            app_services=runtime_context.get("app_services"),
            workspace_dir_override=runtime_context.get("planning_root"),
            allowed_tools=(
                allowed_tools
                if allowed_tools is not None
                else [
                    "read_file",
                    "grep_search",
                    "glob_search",
                ]
            ),
            confine_workspace=True,
        )

        if result["status"] != "success":
            model_name = result.get("model_info", {}).get("model_name", "未知")
            tool_count = result.get("tool_count", "未知")
            elapsed = result.get("elapsed_seconds", 0)
            reason = result.get("error", result["status"])
            if result["status"] == "config_error" and tool_count == 0:
                reason = (
                    f"当前研究会话可用工具数：0，无法查询 GitHub Issues "
                    f"或搜索代码库。模型：{model_name}；"
                    f"最大循环：{result.get('max_iters', phase_max_iters)}；"
                    f"耗时：{elapsed} 秒"
                )
            _log.warning(
                "%s attempt %d/%d failed: %s",
                phase_label,
                attempt,
                _MAX_ATTEMPTS,
                reason,
            )
            if attempt < _MAX_ATTEMPTS and result["status"] != "config_error":
                continue
            if ds:
                ds.status = "failed"
                ds.error = f"{phase_label} 失败：{reason}"
            _dialog_emit(
                plan_id, "failed", ds.error if ds else f"{phase_label} failed"
            )
            _dialog_close(plan_id)
            return None

        response = result.get("response", "")
        response_len = len(response)
        limits = _PHASE_OUTPUT_LIMITS.get(phase_label, (50, 32_000))
        min_chars, max_chars = limits

        _log.info(
            "%s attempt %d: %d chars (limit %d-%d), model=%s, tokens_in=%s, tokens_out=%s",
            phase_label,
            attempt,
            response_len,
            min_chars,
            max_chars,
            result.get("model_info", {}).get("model_name", "?"),
            result.get("usage", {}).get("input_tokens", "?"),
            result.get("usage", {}).get("output_tokens", "?"),
        )

        invalid_response = any(
            marker in response.lower() for marker in _PHASE_FAILURE_MARKERS
        )
        if response_len < min_chars or invalid_response:
            reason = (
                (
                    f"Agent 在 {result.get('max_iters', phase_max_iters)} 次循环内未完成任务。"
                    f"当前研究会话可用工具数：{result.get('tool_count', '未知')}；"
                    f"模型：{result.get('model_info', {}).get('model_name', '未知')}；"
                    f"耗时：{result.get('elapsed_seconds', 0)} 秒"
                )
                if invalid_response
                else f"输出过短（{response_len} 字符 < {min_chars}）"
            )
            _log.warning(
                "%s attempt %d: %s — retrying",
                phase_label,
                attempt,
                reason,
            )
            impossible_tool_config = (
                require_tools and result.get("tool_count") == 0
            )
            if attempt < _MAX_ATTEMPTS and not impossible_tool_config:
                _dialog_emit(
                    plan_id,
                    "retrying",
                    f"{phase_label} {reason}，"
                    f"重试 {attempt + 1}/{_MAX_ATTEMPTS}...",
                )
                continue
            if ds:
                ds.status = "failed"
                ds.error = f"{phase_label} 生成失败：{reason}"
            _dialog_emit(
                plan_id, "failed", ds.error if ds else f"{phase_label} failed"
            )
            _dialog_close(plan_id)
            return None

        if response_len > max_chars:
            _log.warning(
                "%s attempt %d: %d chars > max %d — truncating",
                phase_label,
                attempt,
                response_len,
                max_chars,
            )
            response = response[:max_chars]

        return response

    # All retries exhausted with short responses
    if ds:
        ds.status = "failed"
        ds.error = f"{phase_label} 生成失败：所有尝试输出均过短"
    _dialog_emit(
        plan_id, "failed", ds.error if ds else f"{phase_label} failed"
    )
    _dialog_close(plan_id)
    return None


async def _fallback_monolithic_plan(
    plan_id: str,
    goal: str,
    agent_config: object,
    owner_user_id: str | None,
    owner_agent_id: str,
    original_error: ValueError,
) -> dict | None:
    """Fallback: use the original monolithic prompt when split-phase fails."""
    ds = _dialog_runs.get(plan_id)
    _dialog_emit(plan_id, "retrying", "使用整体式 prompt 重试...")

    result = await _run_task(
        instruction=_build_planning_prompt(goal),
        agent_config=agent_config,
        request_context={
            "session_id": f"research-fallback-{uuid.uuid4().hex[:8]}",
            "user_id": owner_user_id or "research",
            "channel": "console",
            "agent_id": owner_agent_id,
        },
        max_iters=30,
        timeout=900,
        output_dir=None,
    )

    if result["status"] != "success":
        if ds:
            ds.status = "failed"
            ds.error = _format_plan_error(
                result.get("error", result["status"])
            )
        _dialog_emit(plan_id, "failed", ds.error if ds else "回退重试失败")
        _dialog_close(plan_id)
        return None

    response = result.get("response", "")
    _log.info("Fallback monolithic response: %d chars", len(response))

    try:
        return _parse_planning_output(response, goal)
    except ValueError as exc:
        model_name = result.get("model_info", {}).get("model_name", "未知")
        tokens_out = result.get("usage", {}).get("output_tokens", "?")
        if ds:
            ds.status = "failed"
            ds.error = (
                f"规划失败（3 阶段 + 整体式回退均未生成有效文件）\n\n"
                f"错误: {exc}\n"
                f"模型: {model_name}\n"
                f"输出 tokens: {tokens_out}\n\n"
                "💡 建议: 切换模型、简化目标描述、或在设置中检查 API 密钥。"
            )
        _dialog_emit(plan_id, "failed", ds.error if ds else str(exc))
        _dialog_close(plan_id)
        return None


def _extract_phase_content(raw_response: str, filename: str) -> str:
    """Extract the actual content from a phase response.

    LLMs often emit preamble text (e.g. "Now I have enough context...") before
    the ``<<<FILE:name>>>...<<<END>>>`` markers.  This extracts only the
    content between the markers for *filename*, falling back to fenced-code
    extraction and finally to the raw string.
    """
    if not raw_response or not raw_response.strip():
        return raw_response

    # ── Try <<<FILE:name>>>...<<<END>>> markers ──
    escaped = re.escape(filename)
    tag_re = re.compile(
        rf"<<<FILE:\s*{escaped}\s*>>>\s*\n(.*?)<<<END>>>",
        re.DOTALL | re.IGNORECASE,
    )
    m = tag_re.search(raw_response)
    if m:
        content = m.group(1).strip()
        if content:
            _log.debug(
                "_extract_phase_content: %s — extracted %d chars from markers",
                filename,
                len(content),
            )
            return content

    # ── Try fenced code block ```name ... ``` ──
    fence_re = re.compile(
        rf"```(?:python|py|{escaped})\s*\n(.*?)```",
        re.DOTALL | re.IGNORECASE,
    )
    m = fence_re.search(raw_response)
    if m:
        content = m.group(1).strip()
        if content:
            _log.debug(
                "_extract_phase_content: %s — extracted %d chars from fence",
                filename,
                len(content),
            )
            return content

    # ── Fallback: strip common preamble patterns ──
    # If the response starts with natural language and then has code-looking
    # content after a blank line, try to find where code starts.
    _log.debug(
        "_extract_phase_content: %s — no markers found, returning raw (%d chars)",
        filename,
        len(raw_response),
    )
    return raw_response


async def _execute_dialog_plan(
    plan_id: str,
    body: DialogGoalRequest,
) -> None:
    """Background task: plan → create task → start research, pushing SSE events."""
    ds = _dialog_runs.get(plan_id)
    if not ds:
        return

    try:
        owner_agent_id, owner_user_id, owner_session_id = _dialog_owner(ds)

        # ── Phase 1: Planning ──
        ds.status = "planning"
        _dialog_emit(plan_id, "planning", "正在分析你的目标...")

        from ...config.config import load_agent_config, ModelSlotConfig

        agent_config = load_agent_config(owner_agent_id)
        if body.model:
            parts = body.model.split("/", 1)
            agent_config.active_model = ModelSlotConfig(
                provider_id=parts[0] if len(parts) == 2 else "",
                model=parts[-1],
            )

        def _plan_ctx(suffix: str = "") -> dict:
            return {
                "session_id": f"research-{suffix}-{uuid.uuid4().hex[:8]}",
                "user_id": owner_user_id or "research",
                "channel": "console",
                "agent_id": owner_agent_id,
            }

        # ── Discovery: decide what to optimize before planning code ──
        ds.status = "discovering"
        _dialog_emit(plan_id, "discovering", "调研当前实现、根因和候选方向...")
        issue_evidence = await _fetch_github_issue_evidence(body.goal)
        if issue_evidence:
            issue_count = issue_evidence.count("\n#") + int(
                issue_evidence.startswith("#"),
            )
            _dialog_emit(
                plan_id,
                "issues_loaded",
                f"已读取 {issue_count} 个开放 GitHub Issues",
            )
        brief_response = await _run_single_phase(
            plan_id=plan_id,
            instruction=_build_discovery_prompt(
                body.goal,
                body.rounds,
                issue_evidence,
            ),
            agent_config=agent_config,
            request_context=_plan_ctx("discovery"),
            max_iters=_DISCOVERY_MAX_ITERS,
            timeout=180,
            phase_label="research_brief",
            require_tools=False,
            allowed_tools=[],
        )
        if brief_response is None:
            return
        try:
            brief = _parse_research_brief(
                brief_response,
                goal=body.goal,
                rounds=body.rounds,
            )
        except ValueError as exc:
            _dialog_emit(
                plan_id, "retrying", f"ResearchBrief 校验失败，修复格式: {exc}"
            )
            repaired = await _run_single_phase(
                plan_id=plan_id,
                instruction=(
                    _build_discovery_prompt(
                        body.goal,
                        body.rounds,
                        issue_evidence,
                    )
                    + "\nThe previous response failed validation:\n"
                    + str(exc)
                ),
                agent_config=agent_config,
                request_context=_plan_ctx("discovery-repair"),
                max_iters=_DISCOVERY_MAX_ITERS,
                timeout=300,
                phase_label="research_brief",
                require_tools=False,
                allowed_tools=[],
            )
            if repaired is None:
                return
            try:
                brief = _parse_research_brief(
                    repaired,
                    goal=body.goal,
                    rounds=body.rounds,
                )
            except ValueError as repair_error:
                ds.status = "failed"
                ds.error = str(repair_error)
                _dialog_emit(plan_id, "failed", ds.error)
                _dialog_close(plan_id)
                return
        ds.brief = brief.model_dump()
        direction_titles = "、".join(
            direction.title
            for direction in sorted(
                brief.candidate_directions,
                key=lambda direction: direction.priority,
            )[:3]
        )
        _dialog_emit(
            plan_id,
            "discovery_completed",
            f"发现 {len(brief.candidate_directions)} 个方向：{direction_titles}",
        )

        # ── Phase 1a: Generate program.md ──
        _dialog_emit(plan_id, "searching", "搜索代码库和 GitHub Issues...")
        program_md = await _run_single_phase(
            plan_id=plan_id,
            instruction=_build_plan_only_prompt(
                body.goal,
                brief,
                issue_evidence,
            ),
            agent_config=agent_config,
            request_context=_plan_ctx("plan"),
            max_iters=25,
            timeout=600,
            phase_label="program.md",
        )
        if program_md is None:
            return  # error already emitted

        _log.info(
            "Phase 1a: program.md generated (%d chars) for %s",
            len(program_md),
            plan_id,
        )

        # GitHub issue work is repository-level. Keep the generated proposal
        # for approval instead of forcing it into the legacy solution.py
        # single-file benchmark contract.
        task_id_match = re.match(
            r"^TASK_ID:\s*(\S+)", program_md, re.MULTILINE
        )
        ds.task_id = (
            task_id_match.group(1).strip()
            if task_id_match
            else _derive_task_id(body.goal)
        )
        ds.plan_markdown = _extract_phase_content(program_md, "program.md")
        ds.task_title = _task_title(ds.plan_markdown, ds.task_id)
        ds.revision = 1
        ds.content_hash = _plan_content_hash(ds.plan_markdown)
        (
            ds.environment_compatibility,
            ds.current_environment,
            ds.environment_compatibility_reason,
        ) = _plan_environment_compatibility(ds.plan_markdown)
        ds.status = "awaiting_approval"
        if ds.environment_compatibility == "incompatible":
            _dialog_emit(
                plan_id,
                "environment_incompatible",
                ds.environment_compatibility_reason,
            )
        _dialog_emit(
            plan_id,
            "awaiting_approval",
            "研究方案已生成，等待审批后再修改真实仓库。",
        )
        _dialog_close(plan_id)

    except asyncio.CancelledError:
        ds.status = "failed"
        ds.error = "Cancelled"
        _dialog_emit(plan_id, "failed", "Planning was cancelled")
        _dialog_close(plan_id)
    except Exception as exc:
        ds.status = "failed"
        ds.error = f"{type(exc).__name__}: {exc}"
        _dialog_emit(plan_id, "failed", ds.error)
        _dialog_close(plan_id)


_GITHUB_REPOSITORY_RE = re.compile(
    r"https://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?"
    r"(?:[/\s?#]|$)",
)
_ISSUE_NUMBER_RE = re.compile(r"(?:issues?/|#)(\d{1,10})", re.IGNORECASE)
_EXECUTION_TOOLS = [
    "read_file",
    "grep_search",
    "glob_search",
    "write_file",
    "edit_file",
    "append_file",
]


async def _run_process(
    args: list[str],
    *,
    cwd: Path,
    timeout: float = 300,
    env: dict[str, str] | None = None,
) -> str:
    process = await asyncio.create_subprocess_exec(
        *args,
        cwd=str(cwd),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    try:
        stdout, stderr = await asyncio.wait_for(
            process.communicate(),
            timeout=timeout,
        )
    except asyncio.CancelledError:
        process.kill()
        await process.wait()
        raise
    except asyncio.TimeoutError as exc:
        process.kill()
        await process.wait()
        raise RuntimeError(
            f"{args[0]} timed out after {timeout:g} seconds",
        ) from exc
    output = (
        stdout.decode("utf-8", errors="replace")
        + stderr.decode("utf-8", errors="replace")
    ).strip()
    if process.returncode != 0:
        raise RuntimeError(
            f"{' '.join(args[:3])} failed ({process.returncode}): "
            f"{output[-2000:]}",
        )
    return output


def _github_repository(dialog: DialogRunState) -> tuple[str, str, str]:
    text = f"{dialog.goal}\n{dialog.plan_markdown or ''}"
    match = _GITHUB_REPOSITORY_RE.search(text)
    if match is None:
        raise RuntimeError(
            "Approved plan does not contain a GitHub repository URL"
        )
    owner, repository = match.groups()
    return (
        owner,
        repository,
        f"https://github.com/{owner}/{repository}.git",
    )


def _github_remote_identity(remote_url: str) -> tuple[str, str] | None:
    normalized = remote_url.strip()
    match = re.fullmatch(
        r"(?:https?://github\.com/|ssh://git@github\.com/|git@github\.com:)"
        r"([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?/?",
        normalized,
        re.IGNORECASE,
    )
    if match is None:
        return None
    return match.group(1), match.group(2)


async def _prepare_research_worktree(
    dialog: DialogRunState,
) -> tuple[Path, str, str, str]:
    if dialog.worktree_path:
        worktree = Path(dialog.worktree_path)
        if (
            worktree.is_dir()
            and (worktree / ".git").exists()
            and dialog.branch
            and dialog.upstream_repository
            and dialog.push_repository
        ):
            return (
                worktree,
                dialog.branch,
                dialog.upstream_repository,
                dialog.push_repository,
            )
        raise RuntimeError(
            "Existing research worktree is unavailable; preserved progress "
            "cannot be resumed safely"
        )

    owner, repository, clone_url = _github_repository(dialog)
    upstream_repository = f"{owner}/{repository}"
    package_root = Path(__file__).resolve().parents[4]
    source_root: Path | None = None
    push_repository = upstream_repository
    if (package_root / ".git").exists():
        remote_url = await _run_process(
            ["git", "remote", "get-url", "origin"],
            cwd=package_root,
            timeout=30,
        )
        remote_identity = _github_remote_identity(remote_url)
        if (
            remote_identity is not None
            and remote_identity[1].casefold() == repository.casefold()
        ):
            source_root = package_root
            push_repository = f"{remote_identity[0]}/{remote_identity[1]}"

    if source_root is None:
        runtime = _dialog_runtime_context.get(dialog.plan_id, {})
        workspace = runtime.get("workspace")
        workspace_dir = Path(
            getattr(workspace, "workspace_dir", WORKING_DIR),
        ).expanduser()
        source_root = (
            workspace_dir
            / ".qwenpaw"
            / "research-repositories"
            / f"{owner}-{repository}"
        )
        if not (source_root / ".git").exists():
            source_root.parent.mkdir(parents=True, exist_ok=True)
            await _run_process(
                ["git", "clone", clone_url, str(source_root)],
                cwd=source_root.parent,
                timeout=600,
            )

    await _run_process(
        ["git", "fetch", "origin", "main"],
        cwd=source_root,
        timeout=600,
    )
    issue_match = _ISSUE_NUMBER_RE.search(
        f"{dialog.plan_markdown or ''}\n{dialog.goal}",
    )
    issue = issue_match.group(1) if issue_match else "task"
    suffix = re.sub(r"[^a-z0-9]+", "", dialog.plan_id.lower())[:8]
    branch = f"autoresearch/issue-{issue}-{suffix}"
    worktree = source_root / ".qwenpaw" / "worktrees" / f"research-{suffix}"
    worktree.parent.mkdir(parents=True, exist_ok=True)
    await _run_process(
        [
            "git",
            "worktree",
            "add",
            str(worktree),
            "-b",
            branch,
            "origin/main",
        ],
        cwd=source_root,
        timeout=120,
    )
    return (
        worktree,
        branch,
        upstream_repository,
        push_repository,
    )


def _execution_prompt(dialog: DialogRunState, worktree: Path) -> str:
    previous_feedback = ""
    if dialog.validation_attempts or dialog.validation_report:
        previous_feedback = f"""

Previous validation feedback
----------------------------
This is a resumed attempt. Preserve valid changes already present in the
worktree and address the validation feedback instead of starting over.

Previously unapproved paths:
{chr(10).join(f"- {path}" for path in dialog.unapproved_paths) or "- none"}

Latest validation report:
{dialog.validation_report[-12_000:]}
"""
    return f"""You are executing an already approved repository plan.

Approved revision: {dialog.approved_revision}
Approved SHA-256: {dialog.approved_content_hash}
Repository worktree: {worktree}

APPROVED PLAN
{dialog.plan_markdown}
{previous_feedback}

Requirements:
1. Inspect the issue and current code before editing.
2. Make the smallest correct change entirely inside the worktree.
3. Add or update focused regression tests that reproduce the issue's public,
   observable behavior using the approved environment and configuration.
   Prefer local fakes or mocks for credentials, processes, and network calls.
   Do not merely assert private implementation details when a behavior-level
   reproduction is feasible.
4. Do not run tests yourself; the host runs approved tests in a deny-default sandbox.
5. Do not commit, push, create a pull request, or edit files outside the worktree.
6. Only create or edit files listed under ``Modifiable Files``. Keep every
   frozen file byte-for-byte unchanged.
7. Do not create documentation, TODO files, ``.gitignore`` changes, lockfiles,
   skill metadata, generated files, or any other file outside the approved
   scope. If the issue is already implemented, add only the focused regression
   tests listed in the plan.
8. Finish only after the approved changes and tests are present on disk.
"""


def _reproduction_baseline_ref(plan_markdown: str) -> str:
    match = re.search(
        r"(?im)^\s*Baseline Ref:\s*`?([^`\r\n]+?)`?\s*$",
        plan_markdown,
    )
    baseline_ref = match.group(1).strip() if match else "HEAD"
    invalid = (
        not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,199}", baseline_ref)
        or baseline_ref.startswith("-")
        or ".." in baseline_ref
        or "@{" in baseline_ref
        or "//" in baseline_ref
        or baseline_ref.endswith(("/", ".", ".lock"))
    )
    if invalid:
        raise RuntimeError(f"Invalid reproduction baseline ref: {baseline_ref!r}")
    return baseline_ref


async def _prepare_reproduction_baseline(
    worktree: Path,
    test_root: Path,
    test_paths: list[str],
    baseline_ref: str,
    upstream_repository: str = "",
) -> Path:
    resolve_args = [
        "git",
        "rev-parse",
        "--verify",
        "--end-of-options",
        f"{baseline_ref}^{{commit}}",
    ]
    try:
        resolved_sha = await _run_process(
            resolve_args,
            cwd=worktree,
            timeout=30,
        )
    except RuntimeError:
        if upstream_repository and not re.fullmatch(
            r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+",
            upstream_repository,
        ):
            raise RuntimeError(
                f"Invalid upstream GitHub repository: {upstream_repository!r}",
            )
        baseline_remote = (
            f"https://github.com/{upstream_repository}.git"
            if upstream_repository
            else "origin"
        )
        await _run_process(
            [
                "git",
                "fetch",
                "--no-tags",
                "--depth=1",
                baseline_remote,
                baseline_ref,
            ],
            cwd=worktree,
            timeout=300,
        )
        resolved_sha = await _run_process(
            [
                "git",
                "rev-parse",
                "--verify",
                "--end-of-options",
                "FETCH_HEAD^{commit}",
            ],
            cwd=worktree,
            timeout=30,
        )
    baseline = test_root / "baseline"
    await _run_process(
        ["git", "clone", "--shared", "--no-checkout", str(worktree), str(baseline)],
        cwd=test_root,
        timeout=120,
    )
    await _run_process(
        ["git", "checkout", "--detach", resolved_sha.strip()],
        cwd=baseline,
        timeout=120,
    )
    for relative_path in test_paths:
        candidate = worktree / relative_path
        try:
            candidate.resolve().relative_to(worktree.resolve())
        except ValueError as exc:
            raise RuntimeError(
                f"Focused regression test escapes the worktree: {relative_path}",
            ) from exc
        if candidate.is_symlink() or not candidate.is_file():
            raise RuntimeError(
                f"Focused regression test is not a file: {relative_path}",
            )
        destination = baseline / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(candidate, destination)

        for parent in PurePosixPath(relative_path).parents:
            if str(parent) == ".":
                continue
            candidate_modules = worktree / str(parent) / "node_modules"
            baseline_modules = baseline / str(parent) / "node_modules"
            if candidate_modules.is_dir() and not baseline_modules.exists():
                baseline_modules.symlink_to(candidate_modules, target_is_directory=True)
                break
    return baseline


def _focused_test_paths(changed_paths: list[str]) -> list[str]:
    return [
        path
        for path in changed_paths
        if (path.startswith("tests/") and path.endswith(".py"))
        or path.endswith((".test.ts", ".test.tsx", ".test.js", ".test.jsx"))
    ]


async def _run_changed_research_tests(
    root: Path,
    changed_paths: list[str],
    test_root: Path,
) -> list[ResearchTestExecution]:
    test_root.mkdir(parents=True, exist_ok=True)
    test_env = _research_test_environment(root, test_root)
    package_root = Path(__file__).resolve().parents[4]
    python_tests = [
        path
        for path in changed_paths
        if path.startswith("tests/") and path.endswith(".py")
    ]
    node_tests = [
        path
        for path in changed_paths
        if path.endswith((".test.ts", ".test.tsx", ".test.js", ".test.jsx"))
    ]
    executions: list[ResearchTestExecution] = []
    if python_tests:
        python = package_root / ".venv" / "bin" / "python"
        test_args = (
            [str(python), "-m", "pytest", *python_tests, "-q"]
            if python.exists()
            else ["python", "-m", "pytest", *python_tests, "-q"]
        )
        executions.append(
            await _run_research_test(
                test_args,
                cwd=root,
                worktree=root,
                test_root=test_root,
                env=test_env,
            ),
        )
    for package_dir, tests in _group_node_tests(root, node_tests).items():
        relative_package = package_dir.relative_to(root)
        canonical_modules = package_root / relative_package / "node_modules"
        package_modules = package_dir / "node_modules"
        if canonical_modules.is_dir() and not package_modules.exists():
            package_modules.symlink_to(
                canonical_modules,
                target_is_directory=True,
            )
        executions.append(
            await _run_research_test(
                ["npm", "run", "test:run", "--", *tests],
                cwd=package_dir,
                worktree=root,
                test_root=test_root,
                env=test_env,
            ),
        )
    return executions


def _plan_markdown_section(plan_markdown: str, heading: str) -> str:
    match = re.search(
        rf"(?ims)^##\s+{re.escape(heading)}\s*$\n(.*?)(?=^##\s+|\Z)",
        plan_markdown,
    )
    return match.group(1).strip() if match else "Not specified in the approved plan."


def _format_test_evidence(executions: list[ResearchTestExecution]) -> str:
    return "\n\n".join(
        "\n".join(
            (
                f"Command: `{execution.command}`",
                f"Exit code: `{execution.exit_code}`",
                "````text",
                execution.output or "(no output)",
                "````",
            ),
        )
        for execution in executions
    )


def _build_validation_report(
    dialog: DialogRunState,
    changed_paths: list[str],
    baseline_ref: str,
    baseline_executions: list[ResearchTestExecution],
    candidate_executions: list[ResearchTestExecution],
    reproduction_status: str,
    verification_status: str,
) -> str:
    conclusion = (
        "The approved baseline reproduced the issue and the candidate passed "
        "the same focused regression tests."
        if reproduction_status == "reproduced" and verification_status == "passed"
        else "Issue was not reproduced on the approved baseline; no commit or push is allowed."
        if reproduction_status == "not_reproduced"
        else "Validation did not satisfy the evidence gate; no commit or push is allowed."
    )
    files = "\n".join(f"- `{path}`" for path in changed_paths)
    return f"""# AutoResearch Validation Report

## Goal
{dialog.goal}

## Reproduction Environment
{_plan_markdown_section(dialog.plan_markdown or "", "Reproduction Environment")}

## Actual Validation Runtime
- Platform: `{platform.platform()}`
- Python: `{sys.version.split()[0]}`
- Process platform: `{sys.platform}`

## Approved Reproduction Contract
{_plan_markdown_section(dialog.plan_markdown or "", "Reproduction")}

## Approved Verification Contract
{_plan_markdown_section(dialog.plan_markdown or "", "Verification")}

## Changed Files
{files}

## Baseline Reproduction
Baseline ref: `{baseline_ref}`

Status: `{reproduction_status}`

{_format_test_evidence(baseline_executions)}

## Candidate Verification
Status: `{verification_status}`

{_format_test_evidence(candidate_executions)}

## Conclusion
{conclusion}

## Isolation Notes
Tests ran with host credentials removed, network access disabled, and a
deny-default filesystem sandbox. This is execution isolation evidence, not a
proof that the reported operating system or installer was physically used.
"""


async def _validate_and_commit_worktree(
    worktree: Path,
    dialog: DialogRunState,
) -> dict[str, Any]:
    await _run_process(["git", "diff", "--check"], cwd=worktree, timeout=60)
    status = await _run_process(
        ["git", "status", "--porcelain"],
        cwd=worktree,
        timeout=30,
    )
    if not status.strip():
        raise RuntimeError("Agent completed without changing repository files")

    changed_paths = [
        path
        for path in _expanded_changed_paths(
            worktree,
            _changed_paths_from_porcelain(status),
        )
        if not _is_ignored_research_runtime_path(path)
    ]
    if not changed_paths:
        raise RuntimeError("Agent completed without changing repository files")
    approved_paths = _approved_plan_paths(dialog.plan_markdown or "")
    unapproved_paths = [path for path in changed_paths if path not in approved_paths]
    if unapproved_paths:
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
    test_paths = _focused_test_paths(changed_paths)
    if not test_paths:
        raise RuntimeError(
            "Repository changes do not include a focused regression test",
        )

    baseline_ref = _reproduction_baseline_ref(dialog.plan_markdown or "")
    with tempfile.TemporaryDirectory(prefix="qwenpaw-research-test-") as temp:
        test_root = Path(temp)
        baseline = await _prepare_reproduction_baseline(
            worktree,
            test_root,
            test_paths,
            baseline_ref,
            dialog.upstream_repository,
        )
        baseline_executions = await _run_changed_research_tests(
            baseline,
            test_paths,
            test_root / "baseline-runtime",
        )
        candidate_executions = await _run_changed_research_tests(
            worktree,
            test_paths,
            test_root / "candidate-runtime",
        )

    baseline_codes = [execution.exit_code for execution in baseline_executions]
    candidate_codes = [execution.exit_code for execution in candidate_executions]
    if any(code not in {0, 1} for code in baseline_codes):
        reproduction_status = "error"
    elif any(code == 1 for code in baseline_codes):
        reproduction_status = "reproduced"
    else:
        reproduction_status = "not_reproduced"
    if all(code == 0 for code in candidate_codes):
        verification_status = "passed"
    elif any(code not in {0, 1} for code in candidate_codes):
        verification_status = "error"
    else:
        verification_status = "failed"

    reproduction_summary = "; ".join(
        f"{execution.command} -> exit {execution.exit_code}"
        for execution in baseline_executions
    )
    verification_summary = "; ".join(
        f"{execution.command} -> exit {execution.exit_code}"
        for execution in candidate_executions
    )
    validation_report = _build_validation_report(
        dialog,
        changed_paths,
        baseline_ref,
        baseline_executions,
        candidate_executions,
        reproduction_status,
        verification_status,
    )
    ready_to_commit = (
        reproduction_status == "reproduced" and verification_status == "passed"
    )
    result: dict[str, Any] = {
        "ready_to_commit": ready_to_commit,
        "recoverable": (
            not ready_to_commit
            and reproduction_status != "error"
            and verification_status != "error"
        ),
        "failure_category": (
            ""
            if ready_to_commit
            else (
                "not_reproduced"
                if reproduction_status == "not_reproduced"
                else "candidate_validation_failed"
            )
        ),
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
    if not ready_to_commit:
        return result

    await _run_process(
        ["git", "add", "--", *changed_paths],
        cwd=worktree,
        timeout=30,
    )
    await _run_process(
        ["git", "diff", "--cached", "--check"],
        cwd=worktree,
        timeout=60,
    )
    issue_match = _ISSUE_NUMBER_RE.search(
        f"{dialog.plan_markdown or ''}\n{dialog.goal}",
    )
    issue = issue_match.group(1) if issue_match else "research task"
    commit_env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "QwenPaw AutoResearch",
        "GIT_AUTHOR_EMAIL": "autoresearch@qwenpaw.local",
        "GIT_COMMITTER_NAME": "QwenPaw AutoResearch",
        "GIT_COMMITTER_EMAIL": "autoresearch@qwenpaw.local",
    }
    await _run_process(
        ["git", "commit", "-m", f"fix: resolve issue #{issue}"],
        cwd=worktree,
        timeout=120,
        env=commit_env,
    )
    commit_sha = await _run_process(
        ["git", "rev-parse", "HEAD"],
        cwd=worktree,
        timeout=30,
    )
    result["commit_sha"] = commit_sha.strip()
    return result


def _group_node_tests(
    worktree: Path,
    tests: list[str],
) -> dict[Path, list[str]]:
    grouped: dict[Path, list[str]] = {}
    for test in tests:
        test_path = Path(test)
        package_dir = worktree
        relative_test = test_path
        for parent in test_path.parents:
            candidate = worktree / parent / "package.json"
            if candidate.is_file():
                package_dir = candidate.parent
                relative_test = test_path.relative_to(parent)
                break
        grouped.setdefault(package_dir, []).append(str(relative_test))
    return grouped


def _research_test_environment(
    worktree: Path,
    test_root: Path,
) -> dict[str, str]:
    test_home = test_root / "home"
    test_tmp = test_root / "tmp"
    test_home.mkdir()
    test_tmp.mkdir()
    env = {
        "HOME": str(test_home),
        "PATH": os.environ.get("PATH", ""),
        "PYTHONPATH": str(worktree / "src"),
        "TMPDIR": str(test_tmp),
        "QWENPAW_UNSAFE_RESEARCH": "0",
    }
    for key in ("LANG", "LC_ALL", "SYSTEMROOT"):
        value = os.environ.get(key)
        if value:
            env[key] = value
    return env


async def _run_research_test(
    args: list[str],
    *,
    cwd: Path,
    worktree: Path,
    test_root: Path,
    env: dict[str, str],
) -> ResearchTestExecution:
    if sys.platform != "darwin":
        raise RuntimeError(
            "Approved research tests require a supported deny-default OS sandbox",
        )

    from ...sandbox.config import MountSpec, SandboxConfig, SandboxMode
    from ...sandbox.macos_sandbox import MacOSSandbox

    package_root = Path(__file__).resolve().parents[4]
    executable_roots = {
        Path(sys.executable).resolve().parents[1],
        package_root / ".venv",
    }
    for candidate in (
        Path("/bin"),
        Path("/usr/bin"),
        Path("/opt/homebrew"),
        Path("/private/etc"),
        Path("/private/var"),
        Path("/dev"),
    ):
        if candidate.exists():
            executable_roots.add(candidate)
    node_modules = package_root / "console" / "node_modules"
    if node_modules.exists():
        executable_roots.add(node_modules)

    mounts = [
        MountSpec(path=str(worktree.resolve()), writable=True),
        MountSpec(path=str(test_root.resolve()), writable=True),
        *[
            MountSpec(path=str(path.resolve()), writable=False)
            for path in sorted(executable_roots)
            if path.exists()
        ],
    ]
    directory_literals = {Path("/")}
    for mount in mounts:
        path = Path(mount.path)
        directory_literals.update(path.parents)
    literal_rules = " ".join(
        f'(literal "{MacOSSandbox._sanitize_seatbelt_path(str(path))}")'
        for path in sorted(directory_literals)
    )
    config = SandboxConfig(
        mode=SandboxMode.SEATBELT,
        workspace_dir=str(worktree),
        mounts=mounts,
        allow_read_all=False,
        deny_paths=[
            "~/.ssh",
            "~/.aws",
            "~/.config",
            "~/.qwenpaw",
        ],
        network_allow=[],
        timeout_seconds=900,
        env_vars=env,
        env_mode="allowlist",
        platform_hints={
            "seatbelt_extra_rules": (
                "(allow file-read-metadata)\n"
                f"(allow file-read-data {literal_rules})"
            ),
        },
    )
    async with MacOSSandbox(config) as sandbox:
        result = await sandbox.execute(shlex.join(args), cwd=str(cwd))
    output = (result.stdout + result.stderr).strip()
    if result.timed_out:
        raise RuntimeError(f"Sandboxed tests timed out: {shlex.join(args)}")
    if result.sandbox_violation:
        raise RuntimeError(
            f"Sandboxed tests violated the isolation policy: "
            f"{result.sandbox_violation[-2000:]}"
        )
    return ResearchTestExecution(
        command=shlex.join(args),
        exit_code=result.exit_code,
        output=output,
    )


async def _push_research_branch(worktree: Path, branch: str) -> None:
    await _run_process(
        ["git", "push", "-u", "origin", branch],
        cwd=worktree,
        timeout=600,
    )


def _build_dialog_pr_body(dialog: DialogRunState) -> str:
    if not dialog.validation_report.strip():
        raise RuntimeError("Cannot create a PR without a validation report")
    return f"""## AutoResearch Result

- Goal: {dialog.goal}
- Branch: `{dialog.branch}`
- Commit: `{dialog.commit_sha}`

---

{dialog.validation_report.strip()}
"""


async def _create_dialog_pr(worktree: Path, dialog: DialogRunState) -> str:
    if not re.fullmatch(
        r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+",
        dialog.upstream_repository,
    ):
        raise RuntimeError(
            f"Invalid upstream GitHub repository: {dialog.upstream_repository!r}",
        )
    if not re.fullmatch(
        r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+",
        dialog.push_repository,
    ):
        raise RuntimeError(
            f"Invalid push GitHub repository: {dialog.push_repository!r}",
        )
    push_owner = dialog.push_repository.split("/", 1)[0]
    upstream_owner = dialog.upstream_repository.split("/", 1)[0]
    head = (
        dialog.branch
        if push_owner == upstream_owner
        else f"{push_owner}:{dialog.branch}"
    )
    issue_match = _ISSUE_NUMBER_RE.search(
        f"{dialog.plan_markdown or ''}\n{dialog.goal}",
    )
    title = (
        f"fix: resolve issue #{issue_match.group(1)}"
        if issue_match
        else f"fix: {dialog.goal.strip()[:180]}"
    )
    body = _build_dialog_pr_body(dialog)
    if shutil.which("gh") is not None:
        with tempfile.TemporaryDirectory(
            prefix="qwenpaw-research-pr-"
        ) as temp:
            body_file = Path(temp) / "pull-request.md"
            body_file.write_text(body, encoding="utf-8")
            pr_url = await _run_process(
                [
                    "gh",
                    "pr",
                    "create",
                    "--repo",
                    dialog.upstream_repository,
                    "--base",
                    "main",
                    "--head",
                    head,
                    "--title",
                    title,
                    "--body-file",
                    str(body_file),
                ],
                cwd=worktree,
                timeout=120,
            )
        if not pr_url.strip():
            raise RuntimeError("GitHub CLI did not return a pull request URL")
        return pr_url.strip()

    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if not token:
        raise RuntimeError(
            "Cannot create the research PR: install and authenticate GitHub "
            "CLI, or set GITHUB_TOKEN or GH_TOKEN with pull request write "
            "permission"
        )
    payload = {
        "title": title,
        "head": head,
        "base": "main",
        "body": body,
    }
    return await asyncio.to_thread(
        _create_dialog_pr_via_rest,
        dialog.upstream_repository,
        payload,
        token,
    )


def _create_dialog_pr_via_rest(
    repository: str,
    payload: dict[str, str],
    token: str,
) -> str:
    request = UrlRequest(
        f"https://api.github.com/repos/{repository}/pulls",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "QwenPaw-AutoResearch",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=120) as response:
            status = response.status
            raw_response = response.read()
    except HTTPError as exc:
        raise RuntimeError(
            "GitHub API PR creation failed with "
            f"HTTP {exc.code} ({exc.reason})"
        ) from None
    except URLError as exc:
        raise RuntimeError(
            f"GitHub API PR creation failed: {exc.reason}"
        ) from None
    if status != 201:
        raise RuntimeError(
            f"GitHub API PR creation failed with HTTP {status}"
        )
    try:
        response_data = json.loads(raw_response)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise RuntimeError(
            "GitHub API returned an invalid PR creation response"
        ) from exc
    pr_url = response_data.get("html_url")
    if not isinstance(pr_url, str) or not pr_url.strip():
        raise RuntimeError("GitHub API did not return a pull request URL")
    return pr_url.strip()


def _changed_paths_from_porcelain(status: str) -> list[str]:
    entries = status.split("\0") if "\0" in status else status.splitlines()
    paths: list[str] = []
    for entry in entries:
        if len(entry) <= 2:
            continue
        # _run_process strips the first output line, which can remove the
        # leading worktree-status column from entries such as " M src/...".
        prefix_length = 2 if entry[1] == " " and entry[2] != " " else 3
        path = entry[prefix_length:].strip()
        if not path:
            continue
        if " -> " in path:
            path = path.rsplit(" -> ", 1)[1]
        paths.append(path.strip('"'))
    return paths


def _expanded_changed_paths(worktree: Path, paths: list[str]) -> list[str]:
    """Expand Git's aggregate untracked-directory entries into exact files."""
    expanded: list[str] = []
    for path in paths:
        candidate = worktree / path
        if path.endswith("/") and candidate.is_dir():
            expanded.extend(
                child.relative_to(worktree).as_posix()
                for child in sorted(candidate.rglob("*"))
                if child.is_file()
            )
            continue
        expanded.append(path)
    return expanded


def _is_ignored_research_runtime_path(path: str) -> bool:
    """Ignore QwenPaw runtime metadata created while bootstrapping skills."""
    normalized = PurePosixPath(path).as_posix()
    return normalized in _IGNORED_RESEARCH_RUNTIME_PATHS or normalized.startswith(
        ".codegraph/",
    ) or normalized.startswith("research/")


def _approved_plan_paths(plan_markdown: str) -> set[str]:
    candidates = re.findall(
        r"(?<![A-Za-z0-9_.-])"
        r"((?:[A-Za-z0-9_.-]+/)+[A-Za-z0-9_.-]+)"
        r"(?![A-Za-z0-9_./-])",
        plan_markdown,
    )
    approved: set[str] = set()
    for candidate in candidates:
        path = PurePosixPath(candidate)
        if path.is_absolute() or ".." in path.parts:
            continue
        approved.add(path.as_posix())
    return approved


async def _execute_approved_dialog(plan_id: str) -> None:
    """Execute an approved plan in an isolated worktree, then push it."""
    dialog = _dialog_runs.get(plan_id)
    if dialog is None:
        return
    if (
        dialog.status != "approved"
        or dialog.approved_revision != dialog.revision
        or dialog.approved_content_hash != dialog.content_hash
    ):
        return
    compatibility, current_environment, reason = (
        _plan_environment_compatibility(dialog.plan_markdown or "")
    )
    if compatibility == "incompatible":
        _dialog_runs[plan_id] = replace(
            dialog,
            status="failed",
            current_environment=current_environment,
            environment_compatibility=compatibility,
            environment_compatibility_reason=reason,
            error=reason,
            updated_at=_utc_now(),
        )
        _dialog_emit(plan_id, "failed", reason)
        return

    try:
        executing = replace(
            dialog,
            status="executing",
            error="",
            updated_at=_utc_now(),
        )
        _dialog_runs[plan_id] = executing
        _dialog_emit(plan_id, "preparing_worktree", "正在创建隔离 Git 工作树")
        (
            worktree,
            branch,
            upstream_repository,
            push_repository,
        ) = await _prepare_research_worktree(executing)
        executing = replace(
            _dialog_runs[plan_id],
            upstream_repository=upstream_repository,
            push_repository=push_repository,
            worktree_path=str(worktree),
            branch=branch,
            updated_at=_utc_now(),
        )
        _dialog_runs[plan_id] = executing
        _dialog_emit(plan_id, "implementing", f"正在实现 {branch}")

        runtime_context = _dialog_runtime_context.get(plan_id, {})
        from ...config.config import load_agent_config

        agent_config = load_agent_config(executing.owner_agent_id)
        result = await _run_task(
            instruction=_execution_prompt(executing, worktree),
            agent_config=agent_config,
            request_context={
                "session_id": executing.owner_session_id
                or f"research-execute-{plan_id[:8]}",
                "user_id": executing.owner_user_id or "research",
                "channel": "console",
                "agent_id": executing.owner_agent_id,
            },
            max_iters=30,
            timeout=1800,
            output_dir=None,
            require_tools=True,
            workspace=runtime_context.get("workspace"),
            app_services=runtime_context.get("app_services"),
            workspace_dir_override=str(worktree),
            allowed_tools=_EXECUTION_TOOLS,
            confine_workspace=True,
        )
        if result.get("status") != "success":
            raise RuntimeError(
                result.get("error")
                or f"Execution agent failed with status={result.get('status')}",
            )

        _dialog_emit(
            plan_id,
            "reproducing",
            "正在批准的基线版本上复现问题",
        )
        validation = await _validate_and_commit_worktree(
            worktree,
            _dialog_runs[plan_id],
        )
        current = _dialog_runs[plan_id]
        validation_attempt = {
            "attempt": len(current.validation_attempts) + 1,
            "revision": current.revision,
            "timestamp": _utc_now(),
            "failure_category": validation.get("failure_category", ""),
            "changed_paths": list(validation.get("changed_paths", [])),
            "unapproved_paths": list(validation.get("unapproved_paths", [])),
            "reproduction_status": validation["reproduction_status"],
            "reproduction_summary": validation["reproduction_summary"],
            "verification_status": validation["verification_status"],
            "verification_summary": validation["verification_summary"],
            "validation_report": validation["validation_report"],
        }
        evidence = replace(
            current,
            commit_sha=validation["commit_sha"],
            test_summary=validation["test_summary"],
            changed_paths=list(validation.get("changed_paths", [])),
            unapproved_paths=list(validation.get("unapproved_paths", [])),
            validation_failure_category=validation.get(
                "failure_category", ""
            ),
            validation_attempts=[
                *current.validation_attempts,
                validation_attempt,
            ],
            reproduction_status=validation["reproduction_status"],
            reproduction_summary=validation["reproduction_summary"],
            verification_status=validation["verification_status"],
            verification_summary=validation["verification_summary"],
            validation_report=validation["validation_report"],
            updated_at=_utc_now(),
        )
        _dialog_runs[plan_id] = evidence
        _dialog_emit(
            plan_id,
            "report_ready",
            "复现与修复验证报告已生成",
        )
        if not validation["ready_to_commit"]:
            category = validation.get("failure_category", "")
            reason = {
                "scope_mismatch": "计划需要补充本次变更路径后重新审批",
                "not_reproduced": (
                    "问题未能在批准的基线上复现，请修订研究计划后继续"
                ),
                "candidate_validation_failed": (
                    "候选修复验证未通过，可依据现有报告继续研究"
                ),
            }.get(category, "复现或候选验证未通过，已阻止提交和推送")
            recoverable = bool(validation.get("recoverable"))
            failed = replace(
                _dialog_runs[plan_id],
                status="needs_revision" if recoverable else "failed",
                error=reason,
                updated_at=_utc_now(),
            )
            if recoverable:
                proposal, proposal_reason = _build_plan_revision_proposal(
                    failed,
                )
                failed = replace(
                    failed,
                    revision_proposal=proposal,
                    revision_proposal_reason=proposal_reason,
                    revision_proposal_revision=failed.revision,
                )
            _dialog_runs[plan_id] = failed
            _dialog_emit(
                plan_id,
                "validation_needs_revision" if recoverable else "failed",
                reason,
            )
            return

        _dialog_emit(plan_id, "pushing", f"正在推送分支 {branch}")
        await _push_research_branch(worktree, branch)
        pr_url = ""
        if _dialog_runs[plan_id].auto_pr:
            _dialog_emit(
                plan_id,
                "creating_pr",
                "正在创建包含验证报告的 Pull Request",
            )
            pr_url = await _create_dialog_pr(
                worktree,
                _dialog_runs[plan_id],
            )
            _dialog_emit(plan_id, "pr_created", pr_url)
        completed = replace(
            _dialog_runs[plan_id],
            status="completed",
            pr_url=pr_url,
            updated_at=_utc_now(),
        )
        _dialog_runs[plan_id] = completed
        _dialog_emit(
            plan_id,
            "completed",
            f"修复已提交并推送：{completed.commit_sha[:12]}",
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        current = _dialog_runs.get(plan_id, dialog)
        failed = replace(
            current,
            status="failed",
            error=f"{type(exc).__name__}: {exc}",
            updated_at=_utc_now(),
        )
        _dialog_runs[plan_id] = failed
        _dialog_emit(plan_id, "failed", failed.error)
    finally:
        final_state = _dialog_runs.get(plan_id)
        if final_state is None or final_state.status != "needs_revision":
            _dialog_runtime_context.pop(plan_id, None)
        _dialog_close(plan_id)


@router.post("/dialog", status_code=202)
async def dialog_research(
    body: DialogGoalRequest,
    request: Request,
) -> dict[str, Any]:
    """Async: accept goal → start background planning → return plan_id immediately.

    Client should connect to `/research/dialog/{plan_id}/stream` for live progress.
    On completion, the client receives `task_id` + `run_id` to switch to the
    research SSE stream at `/research/runs/{run_id}/stream`.
    """
    if not unsafe_research_enabled():
        raise HTTPException(
            status_code=503,
            detail=(
                "Research evaluator execution is not sandboxed; set "
                f"{UNSAFE_RESEARCH_OPT_IN} to opt in locally"
            ),
        )

    plan_id = uuid.uuid4().hex
    now = _utc_now()
    owner_agent_id, owner_user_id, owner_session_id = _request_owner_identity(
        request,
        session_id=body.session_id,
        user_id=body.user_id,
    )
    ds = DialogRunState(
        plan_id=plan_id,
        status="accepted",
        goal=body.goal,
        events=[],
        owner_agent_id=owner_agent_id,
        owner_user_id=owner_user_id,
        owner_session_id=owner_session_id,
        rounds=body.rounds,
        model=body.model,
        auto_pr=body.auto_pr,
        created_at=now,
        updated_at=now,
    )
    _dialog_runs[plan_id] = ds
    manager = getattr(request.app.state, "multi_agent_manager", None)
    workspace = (
        await manager.get_agent(owner_agent_id)
        if manager is not None
        else None
    )
    _dialog_runtime_context[plan_id] = {
        "workspace": workspace,
        "app_services": getattr(request.app.state, "app_services", None),
        "planning_root": str(Path(__file__).resolve().parents[4]),
    }
    _dialog_tasks[plan_id] = asyncio.create_task(
        _execute_dialog_plan(plan_id, body)
    )

    return {
        "plan_id": plan_id,
        "status": "accepted",
        "stream_url": f"/api/research/dialog/{plan_id}/stream",
    }


@router.get("/dialog/{plan_id}")
async def get_dialog_status(
    plan_id: str,
    request: Request = None,
) -> dict[str, Any]:
    """Poll current dialog planning status."""
    dialog = _recover_legacy_scope_failure(_owned_dialog(plan_id, request))
    _dialog_runs[plan_id] = dialog
    return _dialog_payload(dialog)


@router.put("/dialog/{plan_id}/plan")
async def edit_dialog_plan(
    plan_id: str,
    body: DialogPlanEditRequest,
    request: Request = None,
) -> dict[str, Any]:
    dialog = _recover_legacy_scope_failure(_owned_dialog(plan_id, request))
    _dialog_runs[plan_id] = dialog
    if dialog.status not in {"awaiting_approval", "needs_revision"}:
        raise HTTPException(
            status_code=409, detail="Dialog plan is not editable"
        )
    if body.expected_revision != dialog.revision:
        raise HTTPException(
            status_code=409,
            detail=f"Stale plan revision; current revision is {dialog.revision}",
        )
    now = _utc_now()
    compatibility, current_environment, reason = (
        _plan_environment_compatibility(body.plan_markdown)
    )
    updated = replace(
        dialog,
        status="awaiting_approval",
        plan_markdown=body.plan_markdown,
        task_title=_task_title(
            body.plan_markdown, dialog.task_id or "research"
        ),
        revision=dialog.revision + 1,
        content_hash=_plan_content_hash(body.plan_markdown),
        current_environment=current_environment,
        environment_compatibility=compatibility,
        environment_compatibility_reason=reason,
        revision_proposal="",
        revision_proposal_reason="",
        revision_proposal_revision=None,
        error="",
        updated_at=now,
    )
    _dialog_runs[plan_id] = updated
    _dialog_emit(
        plan_id,
        "plan_updated",
        f"研究方案已更新到 revision {updated.revision}",
    )
    return _dialog_payload(_dialog_runs[plan_id])


@router.post("/dialog/{plan_id}/revision-proposal")
async def propose_dialog_plan_revision(
    plan_id: str,
    body: DialogPlanRevisionRequest,
    request: Request = None,
) -> dict[str, Any]:
    dialog = _recover_legacy_scope_failure(_owned_dialog(plan_id, request))
    _dialog_runs[plan_id] = dialog
    if dialog.status not in {"awaiting_approval", "needs_revision"}:
        raise HTTPException(
            status_code=409,
            detail="Dialog plan cannot be revised in its current state",
        )
    if body.expected_revision != dialog.revision:
        raise HTTPException(
            status_code=409,
            detail=f"Stale plan revision; current revision is {dialog.revision}",
        )
    proposal, reason = _build_plan_revision_proposal(
        dialog,
        body.instruction,
    )
    proposed = replace(
        dialog,
        revision_proposal=proposal,
        revision_proposal_reason=reason,
        revision_proposal_revision=dialog.revision,
        updated_at=_utc_now(),
    )
    _dialog_runs[plan_id] = proposed
    _dialog_emit(plan_id, "plan_revision_proposed", reason)
    return _dialog_payload(_dialog_runs[plan_id])


@router.post("/dialog/{plan_id}/revision-proposal/accept")
async def accept_dialog_plan_revision(
    plan_id: str,
    body: DialogPlanRevisionDecisionRequest,
    request: Request = None,
) -> dict[str, Any]:
    dialog = _owned_dialog(plan_id, request)
    if (
        not dialog.revision_proposal
        or dialog.revision_proposal_revision != dialog.revision
    ):
        raise HTTPException(
            status_code=409,
            detail="No current plan revision proposal is available",
        )
    if body.expected_revision != dialog.revision:
        raise HTTPException(
            status_code=409,
            detail=f"Stale plan revision; current revision is {dialog.revision}",
        )
    await edit_dialog_plan(
        plan_id,
        DialogPlanEditRequest(
            plan_markdown=dialog.revision_proposal,
            expected_revision=dialog.revision,
        ),
        request,
    )
    _dialog_emit(
        plan_id,
        "plan_revision_accepted",
        "修订建议已接受，等待批准执行",
    )
    return _dialog_payload(_dialog_runs[plan_id])


@router.post("/dialog/{plan_id}/revision-proposal/reject")
async def reject_dialog_plan_revision(
    plan_id: str,
    body: DialogPlanRevisionDecisionRequest,
    request: Request = None,
) -> dict[str, Any]:
    dialog = _owned_dialog(plan_id, request)
    if body.expected_revision != dialog.revision:
        raise HTTPException(
            status_code=409,
            detail=f"Stale plan revision; current revision is {dialog.revision}",
        )
    if not dialog.revision_proposal:
        raise HTTPException(
            status_code=409,
            detail="No plan revision proposal is available",
        )
    rejected = replace(
        dialog,
        revision_proposal="",
        revision_proposal_reason="",
        revision_proposal_revision=None,
        updated_at=_utc_now(),
    )
    _dialog_runs[plan_id] = rejected
    _dialog_emit(plan_id, "plan_revision_rejected", "修订建议已拒绝")
    return _dialog_payload(_dialog_runs[plan_id])


@router.post("/dialog/{plan_id}/approve")
async def approve_dialog_plan(
    plan_id: str,
    body: DialogPlanApprovalRequest,
    request: Request = None,
) -> dict[str, Any]:
    dialog = _owned_dialog(plan_id, request)
    if (
        dialog.status in {"approved", "executing", "completed"}
        and dialog.approval_idempotency_key == body.idempotency_key
    ):
        return _dialog_payload(dialog)
    if dialog.status not in {"awaiting_approval", "needs_revision"}:
        raise HTTPException(
            status_code=409, detail="Dialog plan is not awaiting approval"
        )
    if (
        dialog.status == "needs_revision"
        and dialog.validation_failure_category == "scope_mismatch"
        and dialog.approved_revision == dialog.revision
    ):
        raise HTTPException(
            status_code=409,
            detail=(
                "Revise the plan to include or remove paths outside the "
                "approved scope before continuing"
            ),
        )
    if body.expected_revision != dialog.revision:
        raise HTTPException(
            status_code=409,
            detail=f"Stale plan revision; current revision is {dialog.revision}",
        )
    if body.content_hash != dialog.content_hash:
        raise HTTPException(
            status_code=409, detail="Plan content hash changed"
        )

    compatibility, current_environment, reason = (
        _plan_environment_compatibility(dialog.plan_markdown or "")
    )
    dialog = replace(
        dialog,
        current_environment=current_environment,
        environment_compatibility=compatibility,
        environment_compatibility_reason=reason,
        updated_at=_utc_now(),
    )
    _dialog_runs[plan_id] = dialog
    if compatibility == "incompatible":
        raise HTTPException(
            status_code=409,
            detail=f"Plan environment is incompatible: {reason}",
        )

    now = _utc_now()
    approved_by = dialog.owner_user_id or dialog.owner_agent_id
    approved = replace(
        dialog,
        status="approved",
        approved_revision=dialog.revision,
        approved_content_hash=dialog.content_hash,
        approval_idempotency_key=body.idempotency_key,
        approved_by=approved_by,
        approved_at=now,
        updated_at=now,
    )
    _dialog_runs[plan_id] = approved
    _dialog_emit(plan_id, "approved", f"研究方案已由 {approved_by} 批准")
    task = asyncio.create_task(_execute_approved_dialog(plan_id))
    _dialog_tasks[plan_id] = task
    return _dialog_payload(_dialog_runs[plan_id])


@router.post("/dialog/{plan_id}/reject")
async def reject_dialog_plan(
    plan_id: str,
    body: DialogPlanRejectRequest,
    request: Request = None,
) -> dict[str, Any]:
    dialog = _owned_dialog(plan_id, request)
    if dialog.status == "rejected":
        return _dialog_payload(dialog)
    if dialog.status not in {"awaiting_approval", "needs_revision"}:
        raise HTTPException(
            status_code=409, detail="Dialog plan is not awaiting approval"
        )
    if body.expected_revision != dialog.revision:
        raise HTTPException(
            status_code=409,
            detail=f"Stale plan revision; current revision is {dialog.revision}",
        )
    now = _utc_now()
    rejected = replace(
        dialog,
        status="rejected",
        rejected_by=dialog.owner_user_id or dialog.owner_agent_id,
        rejected_at=now,
        rejection_reason=body.reason,
        revision_proposal="",
        revision_proposal_reason="",
        revision_proposal_revision=None,
        error="",
        updated_at=now,
    )
    _dialog_runs[plan_id] = rejected
    _dialog_emit(plan_id, "rejected", body.reason or "研究方案已拒绝")
    _dialog_runtime_context.pop(plan_id, None)
    _dialog_close(plan_id)
    return _dialog_payload(_dialog_runs[plan_id])


@router.get("/dialog/{plan_id}/stream")
async def stream_dialog(plan_id: str, request: Request) -> StreamingResponse:
    """SSE endpoint for dialog planning progress."""
    ds = _recover_legacy_scope_failure(_owned_dialog(plan_id, request))
    _dialog_runs[plan_id] = ds

    q: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue(maxsize=256)
    if plan_id not in _dialog_sse_queues:
        _dialog_sse_queues[plan_id] = set()
    _dialog_sse_queues[plan_id].add(q)

    async def event_generator():
        import json

        try:
            # Replay existing events
            for evt in ds.events:
                yield f"data: {json.dumps({'type': 'event', 'phase': evt['phase'], 'detail': evt.get('detail', '')})}\n\n"

            # If already completed/failed, send final event and close
            if ds.status in (
                "completed",
                "failed",
                "needs_revision",
                "awaiting_approval",
                "rejected",
            ):
                final = {
                    "type": "done",
                    "status": ds.status,
                    "task_id": ds.task_id,
                    "task_title": ds.task_title,
                    "run_id": ds.run_id,
                    "error": ds.error,
                }
                yield f"data: {json.dumps(final)}\n\n"
                return

            # Stream live
            while True:
                if await request.is_disconnected():
                    break
                try:
                    item = await asyncio.wait_for(q.get(), timeout=30)
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                if item is None:
                    # Stream complete — send final state
                    final_ds = _dialog_runs.get(plan_id)
                    final = {
                        "type": "done",
                        "status": final_ds.status if final_ds else "completed",
                        "task_id": final_ds.task_id if final_ds else None,
                        "task_title": (
                            final_ds.task_title if final_ds else None
                        ),
                        "run_id": final_ds.run_id if final_ds else None,
                        "error": final_ds.error if final_ds else "",
                    }
                    yield f"data: {json.dumps(final)}\n\n"
                    break
                yield f"data: {json.dumps(item)}\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            queues = _dialog_sse_queues.get(plan_id)
            if queues is not None:
                queues.discard(q)
                if not queues:
                    _dialog_sse_queues.pop(plan_id, None)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ── SSE stream for live research progress ──────────────────────────────────

_sse_queues: dict[str, set[asyncio.Queue[dict[str, Any] | None]]] = {}


def _sse_broadcast(run_id: str, event: dict[str, Any]) -> None:
    """Push a JSON-serialisable event to every subscriber of this run."""
    queues = _sse_queues.get(run_id)
    if queues:
        dead: list[asyncio.Queue[dict[str, Any] | None]] = []
        for q in queues:
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                dead.append(q)
        for q in dead:
            queues.discard(q)


def _sse_close(run_id: str) -> None:
    """Signal all subscribers that the stream is complete."""
    queues = _sse_queues.pop(run_id, None)
    if queues:
        for q in queues:
            try:
                q.put_nowait(None)
            except asyncio.QueueFull:
                pass


# Hook into existing _report_event to also broadcast via SSE
_original_report_event = _report_event


def _report_event_sse(
    run_id: str,
    phase: str,
    round_number: int | None = None,
    detail: str = "",
) -> None:
    _original_report_event(run_id, phase, round_number, detail)
    current = _runs.get(run_id)
    if current and current.events:
        _sse_broadcast(
            run_id,
            {"type": "event", **asdict(current.events[-1])},
        )


# Hook into _report_outcome to broadcast outcomes
_original_report_outcome = _report_outcome


def _report_outcome_sse(run_id: str, outcome: ResearchOutcome) -> None:
    _original_report_outcome(run_id, outcome)
    _sse_broadcast(
        run_id,
        {
            "type": "outcome",
            "outcome": asdict(outcome),
        },
    )
    # If this is the last round, check run status
    run = _runs.get(run_id)
    if run and run.status in {
        "completed",
        "failed",
        "cancelled",
        "auth_required",
    }:
        _sse_close(run_id)


@router.get("/runs/{run_id}/stream")
async def stream_run(
    run_id: str,
    request: Request,
    after_sequence: int = -1,
) -> StreamingResponse:
    """SSE endpoint: streams live events and outcomes as they happen.

    Query params:
        after_sequence: skip events with sequence <= this value (resumption).
    """
    _owned_run(run_id)  # authorization check before registering a subscriber

    # Create a dedicated queue for this subscriber
    q: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue(maxsize=256)

    # Register this queue in the broadcast set
    if run_id not in _sse_queues:
        _sse_queues[run_id] = set()
    _sse_queues[run_id].add(q)
    # Re-read after registration. Duplicates are safe because events carry a
    # sequence; missing an event here would not be recoverable.
    run = _owned_run(run_id)

    async def event_generator():
        try:
            # Replay existing events for late subscribers (respect after_sequence)
            for event in run.events:
                if event.sequence <= after_sequence:
                    continue
                yield f"data: {json.dumps({'type': 'event', **asdict(event)})}\n\n"

            # Replay existing outcomes
            for outcome in run.outcomes:
                yield f"data: {json.dumps({'type': 'outcome', 'outcome': asdict(outcome)})}\n\n"

            if run.status in _TERMINAL_STATUSES:
                yield f"data: {json.dumps({'type': f'run.{run.status}', 'status': run.status, 'error': run.error})}\n\n"
                yield f"data: {json.dumps({'type': 'done'})}\n\n"
                return

            # Stream live events
            while True:
                if await request.is_disconnected():
                    break
                try:
                    item = await asyncio.wait_for(q.get(), timeout=30)
                except asyncio.TimeoutError:
                    # Send keepalive ping
                    yield ": keepalive\n\n"
                    continue
                if item is None:
                    # Stream complete
                    yield f"data: {json.dumps({'type': 'done'})}\n\n"
                    break
                yield f"data: {json.dumps(item)}\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            # Remove this subscriber's queue on disconnect
            queues = _sse_queues.get(run_id)
            if queues is not None:
                queues.discard(q)
                if not queues:
                    _sse_queues.pop(run_id, None)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# Replace module-level references so _execute_run broadcasts via SSE
_report_event = _report_event_sse  # type: ignore[assignment]
_report_outcome = _report_outcome_sse  # type: ignore[assignment]
