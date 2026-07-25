# -*- coding: utf-8 -*-
"""Portable AutoResearch task discovery and run APIs."""

from __future__ import annotations

import asyncio
import logging
import math
import os
import re
import uuid
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_log = logging.getLogger(__name__)

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ..agent_context import get_current_agent_id
from ..agent_context import get_current_session_id
from ..agent_context import get_current_user_id
from ...cli.research_cmd import _run_with_qwenpaw
from ...cli.task_cmd import _run_task
from ...research import ResearchOutcome
from ...research import (
    evaluate_candidate,
    list_research_history,
    load_task,
    create_research_task,
)
from ...research import UNSAFE_RESEARCH_OPT_IN, unsafe_research_enabled
from ...research_submission import SubmissionResult, submit_solution


router = APIRouter(prefix="/research", tags=["research"])
_VALID_TASK_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_PLANNING_TAG_RE = re.compile(r"<<<FILE:(.+?)>>>\s*\n(.*?)<<<END>>>", re.DOTALL)


class CreateResearchTask(BaseModel):
    task_id: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    program: str = Field(min_length=1, max_length=100_000)
    solution_name: str = Field(min_length=1, max_length=50)
    solution_source: str = Field(min_length=1, max_length=100_000)
    judge_source: str = Field(min_length=1, max_length=100_000)


class StartResearchRun(BaseModel):
    rounds: int = Field(default=3, ge=1, le=100)
    agent_id: str = Field(default="default", min_length=1, max_length=100)
    model: str | None = Field(default=None, max_length=300)


class DialogGoalRequest(BaseModel):
    goal: str = Field(min_length=1, max_length=5000, description="Natural language research goal")
    model: str | None = Field(default=None, max_length=300)
    rounds: int = Field(default=3, ge=1, le=100)
    auto_pr: bool = Field(default=True, description="Automatically create PR after research")


@dataclass(frozen=True)
class ResearchRunEvent:
    phase: str
    timestamp: str
    round: int | None = None
    detail: str = ""


@dataclass
class DialogRunState:
    """Mutable state for the dialog planning + creation pipeline."""
    plan_id: str
    status: str  # "planning" | "creating_task" | "starting_run" | "completed" | "failed"
    goal: str
    events: list[dict[str, Any]]
    task_id: str | None = None
    task_title: str | None = None
    run_id: str | None = None
    error: str = ""
    created_at: str = ""
    updated_at: str = ""


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


_runs: dict[str, ResearchRunState] = {}
_runtime_tasks: dict[str, asyncio.Task[None]] = {}
_run_auto_pr: dict[str, bool] = {}
_dialog_runs: dict[str, DialogRunState] = {}
_dialog_tasks: dict[str, asyncio.Task[None]] = {}
_dialog_sse_queues: dict[str, asyncio.Queue[dict[str, Any] | None]] = {}


def _owner_identity() -> tuple[str, str | None, str | None]:
    return (
        get_current_agent_id(),
        get_current_user_id(),
        get_current_session_id(),
    )


def _run_owner(run: ResearchRunState) -> tuple[str, str | None, str | None]:
    return (run.owner_agent_id, run.owner_user_id, run.owner_session_id)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


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
        raise HTTPException(status_code=404, detail="Research task not found") from exc


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
            if run.task_id == task_id and run.status in {"queued", "running"}
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
    event = ResearchRunEvent(phase, now, round_number, detail)
    _runs[run_id] = replace(
        current,
        events=(*current.events, event),
        current_round=round_number if round_number is not None else current.current_round,
        phase=phase,
        updated_at=now,
    )


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
    solution = await asyncio.to_thread(task.solution.read_text, encoding="utf-8")
    if unsafe_research_enabled():
        evaluation = await asyncio.to_thread(evaluate_candidate, task, solution)
        evaluation_payload = asdict(evaluation)
        if not math.isfinite(evaluation.score):
            evaluation_payload["score"] = None
    else:
        evaluation_payload = {
            "passed": False,
            "score": None,
            "metrics": {},
            "error": (
                "Evaluation disabled because it is not sandboxed; set "
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
                "Research evaluator execution is not sandboxed; set "
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
    _report_event(run_id, outcome.status, outcome.round, outcome.error)


def _report_progress(run_id: str, progress: tuple[int, str, str]) -> None:
    round_number, phase, detail = progress
    _report_event(run_id, phase, round_number, detail)


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
        await _run_with_qwenpaw(
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
        )
        from ...config.config import load_agent_config

        agent_config = load_agent_config(current.agent_id)
        workspace_dir = (
            Path(agent_config.workspace_dir).expanduser()
            if agent_config.workspace_dir
            else task_dir
        )
        submission = await submit_solution(
            task_dir,
            load_task(task_dir).solution,
            workspace_dir,
            page_id=f"research-{run_id[:8]}",
            on_progress=lambda phase: _report_event(run_id, phase),
        )
        if submission is not None:
            _runs[run_id] = replace(_runs[run_id], submission=submission)
            if submission.status == "auth_required":
                _report_event(run_id, "auth_required", detail=submission.verdict)
                _runs[run_id] = replace(
                    _runs[run_id],
                    status="auth_required",
                    finished_at=_utc_now(),
                    error="LeetCode authentication is required",
                )
                return
            if submission.status != "accepted":
                detail = submission.verdict or submission.error
                _report_event(run_id, "submission_failed", detail=detail)
                _runs[run_id] = replace(
                    _runs[run_id],
                    status="failed",
                    finished_at=_utc_now(),
                    error=detail or "Remote submission failed",
                )
                return
            _report_event(run_id, "accepted", detail=submission.verdict)
        _report_event(run_id, "completed")
        _runs[run_id] = replace(
            _runs[run_id],
            status="completed",
            finished_at=_utc_now(),
        )
        # Attempt PR creation after successful research
        await _try_create_pr(run_id, run.task_id)
    except asyncio.CancelledError:
        if _runs[run_id].status != "cancelled":
            _report_event(run_id, "cancelled")
            _runs[run_id] = replace(
                _runs[run_id],
                status="cancelled",
                finished_at=_utc_now(),
                error="Research run was cancelled",
            )
    except Exception as exc:
        _report_event(run_id, "failed", detail=f"{type(exc).__name__}: {exc}")
        _runs[run_id] = replace(
            _runs[run_id],
            status="failed",
            finished_at=_utc_now(),
            error=f"{type(exc).__name__}: {exc}",
        )
    finally:
        _runtime_tasks.pop(run_id, None)


@router.post("/tasks/{task_id}/runs", status_code=202)
async def start_run(task_id: str, body: StartResearchRun) -> dict[str, Any]:
    if not unsafe_research_enabled():
        raise HTTPException(
            status_code=503,
            detail=(
                "Research evaluator execution is not sandboxed; set "
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
    if _active_run(task_id) is not None:
        raise HTTPException(status_code=409, detail="This task is already running")
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
    _runtime_tasks[run_id] = asyncio.create_task(_execute_run(run_id, task.root, body))
    return _run_payload(state)


@router.get("/runs/{run_id}")
async def get_run(run_id: str) -> dict[str, Any]:
    return _run_payload(_owned_run(run_id))


@router.post("/runs/{run_id}/cancel", status_code=202)
async def cancel_run(run_id: str) -> dict[str, Any]:
    run = _owned_run(run_id)
    if run.status not in {"queued", "running"}:
        raise HTTPException(status_code=409, detail="Research run is not active")
    _report_event(run_id, "cancelled")
    _runs[run_id] = replace(
        _runs[run_id],
        status="cancelled",
        finished_at=_utc_now(),
        error="Research run was cancelled",
    )
    task = _runtime_tasks.pop(run_id, None)
    if task is not None:
        task.cancel()
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
            "gh", "pr", "create",
            "--title", f"[AutoResearch] {title}",
            "--body", _build_pr_body(program, task_id, solution),
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
        _report_event(run_id, "pr_failed", detail=f"{type(exc).__name__}: {exc}")


# ── Dialog-based research: plan → create → run → PR ───────────────────────


def _build_planning_prompt(goal: str) -> str:
    """Original monolithic prompt — kept for backward compatibility."""
    return _build_plan_only_prompt(goal)


def _build_plan_only_prompt(goal: str) -> str:
    """Phase 1: Generate program.md — research codebase, describe problem."""
    return (
        "You are a research task planner for the QwenPaw auto-research system.\n"
        "Your job: Research the codebase and produce a structured program.md file.\n"
        "\n"
        "Steps:\n"
        "1. Analyze the goal to understand the problem.\n"
        "2. If the goal mentions GitHub issues, use `gh issue list` / `gh issue view`.\n"
        "3. Search the codebase with `find`, `grep`, or `rg` for the relevant source files.\n"
        "4. Read the target source file(s) to understand current behavior.\n"
        "5. Write a detailed program.md with problem description, acceptance criteria,\n"
        "   relevant file paths, code snippets, and suggested test cases.\n"
        "\n"
        "STOP after 5 tool calls — produce the output below.\n"
        "\n"
        "OUTPUT FORMAT — your ENTIRE response must be:\n"
        "\n"
        "TASK_ID: <kebab-case-id>\n"
        "<<<FILE:program.md>>>\n"
        "# Problem Title\n"
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
        "<<<END>>>\n"
        "\n"
        "⚠️  The <<<FILE:program.md>>> / <<<END>>> delimiters are MANDATORY.\n"
        "⚠️  TASK_ID must be on its own line BEFORE the file block.\n"
        "⚠️  Do NOT output judge.py or solution.py — only program.md.\n"
        "\n"
        f"User goal: {goal}"
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
        'import json, sys, ast\n'
        "\n"
        'with open(sys.argv[1], encoding="utf-8") as f:\n'
        '    source = f.read()\n'
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
        'score = 0  # compute from test results\n'
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
        remaining = _re.search(r"X-RateLimit-Remaining['\"]?\s*:\s*['\"]?(\d+)", raw)
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
        return (
            "🔌 模型连接中断，请稍后重试。\n"
            "💡 可尝试切换其他模型。"
        )

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
    task_id_match = re.search(r"TASK_ID:\s*([A-Za-z0-9][A-Za-z0-9._-]*)", response)
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
                r'\{[^{}]*"files"\s*:\s*\[.*?\][^{}]*\}', re.DOTALL,
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
                    _log.info("Parsed JSON format, got files: %s", list(files.keys()))
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
            k in response for k in ("program.md", "judge.py", "FILE:", '"files"')
        ):
            hint += (
                " 响应中未找到任何文件标记。"
                "请确保使用了正确的响应格式: <<<FILE:program.md>>> ... <<<END>>>"
            )
        raise ValueError(hint)

    # Find solution file
    solution_files = {
        k: v for k, v in files.items()
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
        ("__qwenpaw_invalid_candidate__", "缺少哨兵值检查 (__QWENPAW_INVALID_CANDIDATE__)"),
    ]
    _judge_warnings = [
        msg for keyword, msg in _required_judge_elements
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
    q = _dialog_sse_queues.get(plan_id)
    if q is not None:
        q.put_nowait(event)


def _dialog_close(plan_id: str) -> None:
    q = _dialog_sse_queues.pop(plan_id, None)
    if q is not None:
        q.put_nowait(None)


def _dialog_emit(plan_id: str, phase: str, detail: str = "") -> None:
    event = {"type": "event", "phase": phase, "detail": detail}
    _dialog_broadcast(plan_id, event)
    ds = _dialog_runs.get(plan_id)
    if ds:
        ds.events.append({"phase": phase, "detail": detail, "timestamp": _utc_now()})
        ds.updated_at = _utc_now()


# ── P1: Split-phase planning helpers ───────────────────────────────────────

_RETRY_LIMIT = 2  # per-phase retries

# Per-phase output size limits (characters). These prevent runaway token usage
# while giving each phase enough room. program.md gets the most because it
# includes research notes from codebase exploration.
_PHASE_OUTPUT_LIMITS: dict[str, tuple[int, int]] = {
    # (min_chars, max_chars)
    "program.md": (50, 16_000),
    "solution.py": (20, 32_000),
    "judge.py": (50, 16_000),
}


def _derive_task_id(goal: str) -> str:
    """Derive a kebab-case task ID from the goal string."""
    import re as _re
    # Take first 5 words, lowercase, replace non-alnum with dash
    words = goal.strip().lower().split()[:6]
    slug = "-".join(_re.sub(r"[^a-z0-9-]", "", w) for w in words if _re.sub(r"[^a-z0-9-]", "", w))
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
) -> str | None:
    """Run one phase of the split plan pipeline with retries.

    Returns the raw response text on success, or None (after emitting error) on failure.
    """
    from ...config.config import load_agent_config

    ds = _dialog_runs.get(plan_id)
    for attempt in range(1, _RETRY_LIMIT + 1):
        result = await _run_task(
            instruction=instruction,
            agent_config=agent_config,
            request_context={
                **request_context,
                "session_id": f"{request_context['session_id']}-r{attempt}",
            },
            max_iters=max_iters,
            timeout=timeout,
            output_dir=None,
        )

        if result["status"] != "success":
            _log.warning(
                "%s attempt %d/%d failed: %s",
                phase_label, attempt, _RETRY_LIMIT,
                result.get("error", result["status"]),
            )
            if attempt < _RETRY_LIMIT:
                continue
            # All retries exhausted
            if ds:
                ds.status = "failed"
                ds.error = f"{phase_label} 生成失败（{_RETRY_LIMIT} 次尝试均失败）"
            _dialog_emit(plan_id, "failed", ds.error if ds else f"{phase_label} failed")
            _dialog_close(plan_id)
            return None

        response = result.get("response", "")
        response_len = len(response)
        limits = _PHASE_OUTPUT_LIMITS.get(phase_label, (50, 32_000))
        min_chars, max_chars = limits

        _log.info(
            "%s attempt %d: %d chars (limit %d-%d), model=%s, tokens_in=%s, tokens_out=%s",
            phase_label, attempt, response_len, min_chars, max_chars,
            result.get("model_info", {}).get("model_name", "?"),
            result.get("usage", {}).get("input_tokens", "?"),
            result.get("usage", {}).get("output_tokens", "?"),
        )

        if response_len < min_chars:
            _log.warning(
                "%s attempt %d: %d chars < min %d — retrying",
                phase_label, attempt, response_len, min_chars,
            )
            if attempt < _RETRY_LIMIT:
                _dialog_emit(plan_id, "retrying",
                             f"{phase_label} 输出过短（{response_len} 字符 < {min_chars}），"
                             f"重试 {attempt + 1}/{_RETRY_LIMIT}...")
                continue
            # All retries exhausted with short response
            if ds:
                ds.status = "failed"
                ds.error = f"{phase_label} 生成失败：所有尝试输出均过短"
            _dialog_emit(plan_id, "failed", ds.error if ds else f"{phase_label} failed")
            _dialog_close(plan_id)
            return None

        if response_len > max_chars:
            _log.warning(
                "%s attempt %d: %d chars > max %d — truncating",
                phase_label, attempt, response_len, max_chars,
            )
            response = response[:max_chars]

        return response

    # All retries exhausted with short responses
    if ds:
        ds.status = "failed"
        ds.error = f"{phase_label} 生成失败：所有尝试输出均过短"
    _dialog_emit(plan_id, "failed", ds.error if ds else f"{phase_label} failed")
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
            ds.error = _format_plan_error(result.get("error", result["status"]))
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
                filename, len(content),
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
                filename, len(content),
            )
            return content

    # ── Fallback: strip common preamble patterns ──
    # If the response starts with natural language and then has code-looking
    # content after a blank line, try to find where code starts.
    _log.debug(
        "_extract_phase_content: %s — no markers found, returning raw (%d chars)",
        filename, len(raw_response),
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
        owner_agent_id, owner_user_id, owner_session_id = _owner_identity()

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

        # ── Phase 1a: Generate program.md ──
        _dialog_emit(plan_id, "searching", "搜索代码库和 GitHub Issues...")
        program_md = await _run_single_phase(
            plan_id=plan_id,
            instruction=_build_plan_only_prompt(body.goal),
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
            len(program_md), plan_id,
        )

        # ── Phase 1b: Extract solution code ──
        _dialog_emit(plan_id, "extracting", "提取当前代码作为 baseline...")
        solution_code = await _run_single_phase(
            plan_id=plan_id,
            instruction=_build_solution_prompt(body.goal, program_md),
            agent_config=agent_config,
            request_context=_plan_ctx("solution"),
            max_iters=15,
            timeout=300,
            phase_label="solution.py",
        )
        if solution_code is None:
            return

        _log.info(
            "Phase 1b: solution.py extracted (%d chars) for %s",
            len(solution_code), plan_id,
        )

        # ── Phase 1c: Generate judge.py ──
        _dialog_emit(plan_id, "writing_judge", "编写评测器...")
        judge_code = await _run_single_phase(
            plan_id=plan_id,
            instruction=_build_judge_prompt(body.goal, program_md, solution_code),
            agent_config=agent_config,
            request_context=_plan_ctx("judge"),
            max_iters=10,
            timeout=300,
            phase_label="judge.py",
        )
        if judge_code is None:
            return

        _log.info(
            "Phase 1c: judge.py generated (%d chars) for %s",
            len(judge_code), plan_id,
        )

        # ── Phase 2: Assemble & validate ──
        _dialog_emit(plan_id, "generating", "验证 3 文件契约...")

        # Extract task_id from program_md BEFORE stripping markers
        task_id_match = re.match(r"^TASK_ID:\s*(\S+)", program_md, re.MULTILINE)
        task_id = task_id_match.group(1).strip() if task_id_match else _derive_task_id(body.goal)

        # Clean each phase output — strip LLM preamble, extract only the
        # intended content from <<<FILE:...>>> markers (if present).
        program_content = _extract_phase_content(program_md, "program.md")
        solution_content = _extract_phase_content(solution_code, "solution.py")
        judge_content = _extract_phase_content(judge_code, "judge.py")

        assembled = f"TASK_ID: {task_id}\n"
        assembled += f"<<<FILE:program.md>>>\n{program_content}\n<<<END>>>\n"
        assembled += f"<<<FILE:solution.py>>>\n{solution_content}\n<<<END>>>\n"
        assembled += f"<<<FILE:judge.py>>>\n{judge_content}\n<<<END>>>\n"

        try:
            task_files = _parse_planning_output(assembled, body.goal)
        except ValueError as exc:
            _log.warning("Assembled parse failed: %s", exc)
            # Last resort: retry with monolithic prompt
            _dialog_emit(plan_id, "retrying", "组装验证失败，回退到整体重试...")
            task_files = await _fallback_monolithic_plan(
                plan_id, body.goal, agent_config, owner_user_id, owner_agent_id, exc,
            )
            if task_files is None:
                return

        _log.info(
            "Parsed task: id=%s, files=%s, judge_source=%r",
            task_files.get("task_id"),
            [k for k in task_files],
            (task_files.get("judge_source", "") or "")[:500],
        )

        ds.status = "creating_task"
        ds.task_id = task_files["task_id"]
        ds.task_title = _task_title(task_files["program"], task_files["task_id"])
        _dialog_emit(
            plan_id,
            "creating_task",
            f"创建研究任务: {ds.task_id} — {ds.task_title}",
        )

        root = get_research_root()
        try:
            task = await asyncio.to_thread(
                create_research_task,
                root,
                task_files["task_id"],
                task_files["program"],
                task_files["solution_name"],
                task_files["solution_source"],
                task_files["judge_source"],
            )
        except ValueError as exc:
            ds.status = "failed"
            ds.error = f"Task creation failed: {exc}"
            _dialog_emit(plan_id, "failed", ds.error)
            _dialog_close(plan_id)
            return

        # ── Phase 3: Start research run ──
        ds.status = "starting_run"
        run_id = uuid.uuid4().hex
        ds.run_id = run_id
        _dialog_emit(plan_id, "starting_run", f"启动研究引擎 ({body.rounds} 轮)...")

        created_at = _utc_now()
        queued = ResearchRunEvent("queued", created_at)
        state = ResearchRunState(
            id=run_id,
            task_id=task_files["task_id"],
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
        _run_auto_pr[run_id] = body.auto_pr
        _runtime_tasks[run_id] = asyncio.create_task(
            _execute_run(
                run_id,
                task.root,
                StartResearchRun(
                    rounds=body.rounds,
                    agent_id=owner_agent_id,
                    model=body.model,
                ),
            )
        )

        ds.status = "completed"
        _dialog_emit(
            plan_id,
            "completed",
            f"研究已启动: task={ds.task_id}, run={run_id}",
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


@router.post("/dialog", status_code=202)
async def dialog_research(body: DialogGoalRequest) -> dict[str, Any]:
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
    ds = DialogRunState(
        plan_id=plan_id,
        status="accepted",
        goal=body.goal,
        events=[],
        created_at=now,
        updated_at=now,
    )
    _dialog_runs[plan_id] = ds
    _dialog_tasks[plan_id] = asyncio.create_task(_execute_dialog_plan(plan_id, body))

    return {
        "plan_id": plan_id,
        "status": "accepted",
        "stream_url": f"/api/research/dialog/{plan_id}/stream",
    }


@router.get("/dialog/{plan_id}")
async def get_dialog_status(plan_id: str) -> dict[str, Any]:
    """Poll current dialog planning status."""
    ds = _dialog_runs.get(plan_id)
    if not ds:
        raise HTTPException(status_code=404, detail="Unknown dialog plan")
    return {
        "plan_id": ds.plan_id,
        "status": ds.status,
        "goal": ds.goal,
        "task_id": ds.task_id,
        "task_title": ds.task_title,
        "run_id": ds.run_id,
        "error": ds.error,
        "events": ds.events,
        "created_at": ds.created_at,
        "updated_at": ds.updated_at,
    }


@router.get("/dialog/{plan_id}/stream")
async def stream_dialog(plan_id: str, request: Request) -> StreamingResponse:
    """SSE endpoint for dialog planning progress."""
    ds = _dialog_runs.get(plan_id)
    if not ds:
        raise HTTPException(status_code=404, detail="Unknown dialog plan")

    q: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
    existing = _dialog_sse_queues.get(plan_id)
    if existing is not None:
        q = existing
    else:
        _dialog_sse_queues[plan_id] = q

    async def event_generator():
        import json

        # Replay existing events
        for evt in ds.events:
            yield f"data: {json.dumps({'type': 'event', 'phase': evt['phase'], 'detail': evt.get('detail', '')})}\n\n"

        # If already completed/failed, send final event and close
        if ds.status in ("completed", "failed"):
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
        try:
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
                        "task_title": final_ds.task_title if final_ds else None,
                        "run_id": final_ds.run_id if final_ds else None,
                        "error": final_ds.error if final_ds else "",
                    }
                    yield f"data: {json.dumps(final)}\n\n"
                    break
                yield f"data: {json.dumps(item)}\n\n"
        except asyncio.CancelledError:
            pass

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

_sse_queues: dict[str, asyncio.Queue[dict[str, Any] | None]] = {}


def _sse_broadcast(run_id: str, event: dict[str, Any]) -> None:
    """Push a JSON-serialisable event to every subscriber of this run."""
    q = _sse_queues.get(run_id)
    if q is not None:
        q.put_nowait(event)


def _sse_close(run_id: str) -> None:
    """Signal all subscribers that the stream is complete."""
    q = _sse_queues.pop(run_id, None)
    if q is not None:
        q.put_nowait(None)


# Hook into existing _report_event to also broadcast via SSE
_original_report_event = _report_event


def _report_event_sse(
    run_id: str,
    phase: str,
    round_number: int | None = None,
    detail: str = "",
) -> None:
    _original_report_event(run_id, phase, round_number, detail)
    _sse_broadcast(
        run_id,
        {
            "type": "event",
            "phase": phase,
            "round": round_number,
            "detail": detail,
        },
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
    if run and run.status in {"completed", "failed", "cancelled", "auth_required"}:
        _sse_close(run_id)


@router.get("/runs/{run_id}/stream")
async def stream_run(run_id: str, request: Request) -> StreamingResponse:
    """SSE endpoint: streams live events and outcomes as they happen."""
    run = _owned_run(run_id)

    # Create a queue for this subscriber
    q: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()

    # If a queue already exists, reuse it; otherwise register new one
    existing = _sse_queues.get(run_id)
    if existing is not None:
        q = existing
    else:
        _sse_queues[run_id] = q

    async def event_generator():
        import json

        # Replay existing events for late subscribers
        for event in run.events:
            yield f"data: {json.dumps({'type': 'event', 'phase': event.phase, 'round': event.round, 'detail': event.detail})}\n\n"

        # Replay existing outcomes
        for outcome in run.outcomes:
            yield f"data: {json.dumps({'type': 'outcome', 'outcome': asdict(outcome)})}\n\n"

        # Stream live events
        try:
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
                    break
                yield f"data: {json.dumps(item)}\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            pass  # Don't remove the queue — other subscribers may be listening

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
