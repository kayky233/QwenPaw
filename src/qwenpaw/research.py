from __future__ import annotations

import ast
import asyncio
import hashlib
import json
import math
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unicodedata
from collections.abc import Awaitable
from collections.abc import Callable
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from datetime import timezone
from pathlib import Path
from typing import Any


MAX_CANDIDATE_CHARS = 100_000
_VALID_TASK_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
UNSAFE_RESEARCH_OPT_IN = "QWENPAW_UNSAFE_RESEARCH=1"


def unsafe_research_enabled() -> bool:
    """Require an explicit local opt-in until evaluator sandboxing exists."""
    return os.environ.get("QWENPAW_UNSAFE_RESEARCH") == "1"


@dataclass(frozen=True)
class ResearchTask:
    root: Path
    program: Path
    solution: Path
    judge: Path
    database: Path


@dataclass(frozen=True)
class Evaluation:
    passed: bool
    score: float
    metrics: Mapping[str, Any]
    error: str = ""


@dataclass(frozen=True)
class ResearchOutcome:
    round: int
    status: str
    passed: bool
    baseline_score: float
    candidate_score: float | None
    improvement: float
    metrics: Mapping[str, Any]
    error: str = ""


def load_task(task_dir: Path, database_path: Path | None = None) -> ResearchTask:
    root = task_dir.expanduser().resolve()
    program = root / "program.md"
    judge = root / "judge.py"
    solutions = sorted(path for path in root.glob("solution.*") if path.is_file())
    if not root.is_dir():
        raise ValueError(f"task directory does not exist: {root}")
    if not program.is_file() or not judge.is_file() or len(solutions) != 1:
        raise ValueError(
            "task requires program.md, judge.py, and exactly one solution.* file",
        )
    database = database_path or root / ".research" / "experiments.sqlite"
    return ResearchTask(root, program, solutions[0], judge, database)


def create_research_task(
    research_root: Path,
    task_id: str,
    program: str,
    solution_name: str,
    solution_source: str,
    judge_source: str,
) -> ResearchTask:
    """Create a portable task only after its immutable judge accepts baseline."""
    if not _VALID_TASK_ID.fullmatch(task_id):
        raise ValueError("task_id must contain only letters, numbers, '.', '_' or '-'")
    if (
        Path(solution_name).name != solution_name
        or not solution_name.startswith("solution.")
        or not Path(solution_name).suffix
    ):
        raise ValueError("solution_name must be a plain solution.<extension> filename")
    for name, content in (
        ("program", program),
        ("solution", solution_source),
        ("judge", judge_source),
    ):
        if not content.strip():
            raise ValueError(f"{name} must not be empty")
        if len(content) > MAX_CANDIDATE_CHARS:
            raise ValueError(f"{name} is too large")

    root = research_root.expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    destination = (root / task_id).resolve()
    if destination.parent != root:
        raise ValueError("task directory must stay inside the research root")
    if destination.exists():
        raise ValueError(f"research task already exists: {task_id}")

    draft = Path(tempfile.mkdtemp(prefix=".draft-", dir=root))
    try:
        (draft / "program.md").write_text(program, encoding="utf-8")
        (draft / solution_name).write_text(solution_source, encoding="utf-8")
        (draft / "judge.py").write_text(judge_source, encoding="utf-8")
        task = load_task(draft)
        baseline = evaluate_candidate(task, solution_source)
        # Baseline passed=false is acceptable — the judge may give a negative score
        # to buggy code while still being runnable. We only verify the judge rejects
        # a clearly invalid probe (which proves the judge isn't a no-op).
        if evaluate_candidate(
            task,
            "__QWENPAW_INVALID_CANDIDATE__\n",
        ).passed:
            raise ValueError("judge accepts the invalid probe")
        os.replace(draft, destination)
        return load_task(destination)
    finally:
        if draft.exists():
            shutil.rmtree(draft)


_CONFUSABLE_UNICODE = {
    # Common confusable characters that LLMs sometimes emit
    "\u2018": "'",   # LEFT SINGLE QUOTATION MARK
    "\u2019": "'",   # RIGHT SINGLE QUOTATION MARK
    "\u201c": '"',   # LEFT DOUBLE QUOTATION MARK
    "\u201d": '"',   # RIGHT DOUBLE QUOTATION MARK
    "\u2013": "-",   # EN DASH
    "\u2014": "--",  # EM DASH
    "\u00a0": " ",   # NO-BREAK SPACE
    "\u200b": "",    # ZERO WIDTH SPACE
    "\u200c": "",    # ZERO WIDTH NON-JOINER
    "\u200d": "",    # ZERO WIDTH JOINER
    "\ufeff": "",    # BYTE ORDER MARK / ZERO WIDTH NO-BREAK SPACE
    "\u2026": "...", # HORIZONTAL ELLIPSIS
    "\u00ad": "",    # SOFT HYPHEN
}


def _preflight_candidate(source: str) -> tuple[bool, str]:
    """Validate candidate code before passing to the judge.

    Returns (is_valid, error_message). If is_valid is True, error_message
    is empty.  Detects:

    1. Confusable Unicode that breaks Python syntax (smart quotes, etc.)
    2. Python syntax errors via AST compilation
    """
    if not source or not source.strip():
        return False, "candidate is empty"

    # ── Check for confusable Unicode ──
    issues: list[str] = []
    for char in sorted(set(source)):
        if char in _CONFUSABLE_UNICODE:
            replacement = _CONFUSABLE_UNICODE[char]
            name = unicodedata.name(char, f"U+{ord(char):04X}")
            issues.append(f"U+{ord(char):04X} ({name}) → '{replacement}'")
    if issues:
        return False, "confusable Unicode: " + "; ".join(issues[:5])

    # ── AST compilation check ──
    try:
        ast.parse(source)
    except SyntaxError as exc:
        return False, f"SyntaxError at line {exc.lineno}, col {exc.offset}: {exc.msg}"

    return True, ""


def evaluate_candidate(
    task_or_dir: ResearchTask | Path,
    source: str,
    timeout_seconds: float = 10.0,
) -> Evaluation:
    task = task_or_dir if isinstance(task_or_dir, ResearchTask) else load_task(task_or_dir)
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")
    if not source.strip():
        return Evaluation(False, float("-inf"), {}, "candidate is empty")
    if len(source) > MAX_CANDIDATE_CHARS:
        return Evaluation(False, float("-inf"), {}, "candidate is too large")

    with tempfile.TemporaryDirectory(prefix="qwenpaw_research_") as temporary_dir:
        candidate = Path(temporary_dir) / task.solution.name
        candidate.write_text(source, encoding="utf-8")
        candidate.chmod(0o700)
        interpreter = sys.executable
        for attempt in range(2):
            try:
                completed = subprocess.run(
                    [interpreter, str(task.judge), str(candidate)],
                    cwd=task.root,
                    text=True,
                    stdin=subprocess.DEVNULL,
                    capture_output=True,
                    timeout=timeout_seconds,
                    check=False,
                )
                break
            except subprocess.TimeoutExpired:
                return Evaluation(False, float("-inf"), {}, "judge timed out")
            except FileNotFoundError:
                fallback = shutil.which("python3")
                if attempt or not fallback:
                    return Evaluation(False, float("-inf"), {}, "Python interpreter not found")
                interpreter = fallback

    if completed.returncode != 0:
        error = completed.stderr.strip() or completed.stdout.strip()
        return Evaluation(False, float("-inf"), {}, error[-2000:] or "judge failed")
    try:
        payload = json.loads(completed.stdout)
        passed = payload["passed"]
        score = float(payload["score"])
        metrics = payload.get("metrics", {})
        error = payload.get("error", "")
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        return Evaluation(False, float("-inf"), {}, f"invalid judge output: {exc}")
    if type(passed) is not bool or not math.isfinite(score):
        return Evaluation(False, float("-inf"), {}, "invalid judge result types")
    if not isinstance(metrics, dict) or not isinstance(error, str):
        return Evaluation(False, float("-inf"), {}, "invalid judge metadata")
    return Evaluation(passed, score, metrics, error)


def _extract_candidate(response: str) -> str:
    match = re.search(r"```[^\n]*\n(.*?)```", response, re.DOTALL)
    candidate = (match.group(1) if match else response).strip()
    if not match and candidate.startswith("`") and candidate.endswith("`"):
        candidate = candidate[1:-1].strip()
    if not candidate:
        raise ValueError("proposer returned no candidate")
    if len(candidate) > MAX_CANDIDATE_CHARS:
        raise ValueError("candidate is too large")
    return candidate + "\n"


def _proposal_summary(response: str, parent: str, candidate: str) -> str:
    """Extract a human-readable summary from the LLM proposal."""
    # Try to extract reasoning before the code block
    reasoning = response.split("```")[0].strip() if "```" in response else ""
    if not reasoning:
        # Use diff stats as fallback
        added = sum(1 for l in candidate.split("\n") if l not in parent.split("\n"))
        removed = sum(1 for l in parent.split("\n") if l not in candidate.split("\n"))
        reasoning = f"代码变更: +{added}/-{removed} 行"
    else:
        # Truncate long reasoning
        lines = reasoning.split("\n")
        reasoning = "\n".join(lines[:6])
        if len(lines) > 6:
            reasoning += f"\n... (共 {len(lines)} 行分析)"
    return reasoning


def _outcome_summary(
    outcome: "ResearchOutcome",
    baseline_score: float,
    parent: str,
    candidate: str,
) -> str:
    """Generate a summary for the outcome event."""
    status_emoji = "✅" if outcome.status == "kept" else "❌"
    score_change = f"评分 {baseline_score:.1f} → {outcome.candidate_score or 0:.1f}"
    lines_changed = abs(len(candidate.split("\n")) - len(parent.split("\n")))
    return f"{status_emoji} {status_emoji} {score_change}, 变更 {lines_changed} 行"


def _build_prompt(
    task: ResearchTask,
    source: str,
    outcomes: list[ResearchOutcome],
) -> str:
    feedback = "\n".join(
        f"round={item.round} status={item.status} "
        f"score={item.candidate_score} error={item.error}"
        for item in outcomes[-5:]
    )
    language = task.solution.suffix.removeprefix(".")
    return (
        task.program.read_text(encoding="utf-8").strip()
        + "\n\nReturn only the complete candidate source. Do not use tools or edit files."
        + f"\n\nCURRENT BEST\n```{language}\n{source}```"
        + "\n\nRECENT EVALUATOR FEEDBACK\n"
        + (feedback or "none")
        + "\n"
    )


def _record_run(
    task: ResearchTask,
    outcome: ResearchOutcome,
    parent_source: str,
    candidate_source: str,
) -> None:
    task.database.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(task.database) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                round INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                status TEXT NOT NULL,
                parent_sha TEXT NOT NULL,
                candidate_sha TEXT NOT NULL,
                passed INTEGER NOT NULL,
                baseline_score REAL NOT NULL,
                candidate_score REAL,
                improvement REAL NOT NULL,
                metrics_json TEXT NOT NULL,
                error TEXT NOT NULL,
                parent_source TEXT NOT NULL,
                candidate_source TEXT NOT NULL
            )
            """,
        )
        connection.execute(
            """
            INSERT INTO runs (
                round, created_at, status, parent_sha, candidate_sha, passed,
                baseline_score, candidate_score, improvement, metrics_json,
                error, parent_source, candidate_source
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                outcome.round,
                datetime.now(timezone.utc).isoformat(),
                outcome.status,
                hashlib.sha256(parent_source.encode()).hexdigest(),
                hashlib.sha256(candidate_source.encode()).hexdigest(),
                int(outcome.passed),
                outcome.baseline_score,
                outcome.candidate_score,
                outcome.improvement,
                json.dumps(outcome.metrics, ensure_ascii=False, sort_keys=True),
                outcome.error,
                parent_source,
                candidate_source,
            ),
        )


def _replace_solution(path: Path, source: str) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(source, encoding="utf-8")
    os.replace(temporary, path)


async def run_research(
    task_dir: Path,
    proposer: Callable[[str], Awaitable[str]],
    rounds: int,
    *,
    database_path: Path | None = None,
    judge_timeout: float = 10.0,
    min_improvement: float = 0.0,
    on_outcome: Callable[[ResearchOutcome], None] | None = None,
    on_progress: Callable[[tuple[int, str, str]], None] | None = None,
) -> tuple[ResearchOutcome, ...]:
    if rounds < 1:
        raise ValueError("rounds must be at least 1")
    if min_improvement < 0:
        raise ValueError("min_improvement must be non-negative")
    task = load_task(task_dir, database_path)
    source = task.solution.read_text(encoding="utf-8")
    outcomes: list[ResearchOutcome] = []

    for round_number in range(1, rounds + 1):
        parent_source = source
        if on_progress is not None:
            on_progress((round_number, "evaluating_baseline", f"评测第 {round_number} 轮基线方案"))
        baseline = await asyncio.to_thread(
            evaluate_candidate,
            task,
            source,
            judge_timeout,
        )
        if not baseline.passed:
            # Baseline didn't pass the judge, but the proposer may still fix it.
            # Record the outcome and let the LLM attempt to improve the code.
            if on_progress is not None:
                on_progress((round_number, "baseline_failing", f"基线未通过: {baseline.error[:120]}"))
        try:
            if on_progress is not None:
                on_progress((round_number, "proposing", f"AI 分析代码中 (第 {round_number} 轮)..."))
            response = await proposer(_build_prompt(task, source, outcomes))
            candidate = _extract_candidate(response)

            # ── Preflight Gate: validate candidate before expensive judge run ──
            for repair_attempt in range(3):  # 0 = first try, 1-2 = repairs
                valid, preflight_error = _preflight_candidate(candidate)
                if valid:
                    break
                if repair_attempt < 2:
                    if on_progress is not None:
                        on_progress((
                            round_number,
                            "preflight_repair",
                            f"候选代码预检失败，请求修复 (第 {repair_attempt + 1}/2 次): {preflight_error[:100]}",
                        ))
                    repair_response = await proposer(
                        _build_prompt(task, source, outcomes)
                        + f"\n\n[SYSTEM] 上次生成的代码无法通过预检: {preflight_error}\n"
                        "请修复语法问题和异常 Unicode 字符后重新生成代码。\n"
                        "确保代码直接放入 ```python 代码块中，使用标准 ASCII 引号和缩进。"
                    )
                    candidate = _extract_candidate(repair_response)
                else:
                    # All repair attempts exhausted — record preflight_error outcome
                    outcome = ResearchOutcome(
                        round_number,
                        "preflight_error",
                        False,
                        baseline.score,
                        None,
                        0.0,
                        {},
                        f"preflight failed after 2 repairs: {preflight_error}",
                    )
                    outcomes.append(outcome)
                    if on_outcome is not None:
                        on_outcome(outcome)
                    continue  # skip to next round

            # Send proposal summary as an event
            proposal_preview = _proposal_summary(response, source, candidate)
            if on_progress is not None:
                on_progress((round_number, "proposal", proposal_preview))
            if on_progress is not None:
                on_progress((round_number, "evaluating_candidate", f"评测第 {round_number} 轮候选方案..."))
            evaluation = await asyncio.to_thread(
                evaluate_candidate,
                task,
                candidate,
                judge_timeout,
            )
            improvement = evaluation.score - baseline.score if evaluation.passed else 0.0
            status = (
                "kept"
                if evaluation.passed and improvement > min_improvement
                else "rejected"
            )
            outcome = ResearchOutcome(
                round_number,
                status,
                evaluation.passed,
                baseline.score,
                evaluation.score if evaluation.passed else None,
                improvement,
                evaluation.metrics,
                evaluation.error,
            )
            # Send rich outcome detail
            if on_progress is not None:
                detail = _outcome_summary(outcome, baseline.score, source, candidate)
                on_progress((round_number, status, detail))
        except Exception as exc:
            candidate = ""
            outcome = ResearchOutcome(
                round_number,
                "proposer_error",
                False,
                baseline.score,
                None,
                0.0,
                {},
                f"{type(exc).__name__}: {exc}",
            )

        if outcome.status == "kept":
            _replace_solution(task.solution, candidate)
            source = candidate
        _record_run(task, outcome, parent_source, candidate)
        outcomes.append(outcome)
        if on_outcome is not None:
            on_outcome(outcome)

    return tuple(outcomes)


def list_research_history(
    task_or_dir: ResearchTask | Path,
    limit: int = 50,
) -> tuple[dict[str, Any], ...]:
    """Read recent evaluator outcomes without exposing stored source snapshots."""
    if limit < 1:
        raise ValueError("limit must be at least 1")
    task = task_or_dir if isinstance(task_or_dir, ResearchTask) else load_task(task_or_dir)
    if not task.database.is_file():
        return ()
    with sqlite3.connect(task.database) as connection:
        rows = connection.execute(
            """
            SELECT round, created_at, status, passed, baseline_score,
                   candidate_score, improvement, metrics_json, error
            FROM runs ORDER BY id DESC LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return tuple(
        {
            "round": row[0],
            "created_at": row[1],
            "status": row[2],
            "passed": bool(row[3]),
            "baseline_score": row[4],
            "candidate_score": row[5],
            "improvement": row[6],
            "metrics": json.loads(row[7]),
            "error": row[8],
        }
        for row in rows
    )
