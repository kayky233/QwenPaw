from __future__ import annotations

import ast
import asyncio
import hashlib
import json
import math
import os
import platform
import re
try:
    import resource
except ImportError:
    resource = None  # Windows — no rlimit support
import shutil
import sqlite3
import stat
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

# ── P1: Phase-level attempt limits (first attempt + N retries) ──
_PLAN_MAX_ATTEMPTS = 3      # first + 2 retries for plan generation
_PROGRAM_MAX_ATTEMPTS = 3   # first + 2 preflight repairs
_JUDGE_MAX_ATTEMPTS = 2      # first + 1 retry on timeout/invalid output
_REVIEW_MAX_ATTEMPTS = 1     # deterministic, no retry needed

# ── P3: Resource limits for judge sandbox ──
_SANDBOX_MEMORY_MB = 512    # virtual memory ceiling
_SANDBOX_CPU_HARD = 300      # 5-minute hard CPU ceiling
_SANDBOX_FSIZE_MB = 10       # max file size judge can create (Unix only, best-effort)
# ── P3: Output capture contract ──
# Parent read cap:   _MAX_READ_BYTES = 64 KiB (cross-platform, guaranteed)
# Unix child limit:   RLIMIT_FSIZE  = 10 MiB (best-effort, runner sets on itself)
# Windows child limit: none — output may grow until disk full; residual risk accepted
_MAX_READ_BYTES = 64 * 1024  # 64 KiB — max parent-side read per stdout/stderr
_SUPPORTS_RLIMITS = os.name == "posix" and resource is not None


class _CancelledError(Exception):
    """Internal signal raised when is_cancelled() returns True mid-pipeline."""


def unsafe_research_enabled() -> bool:
    """Require an explicit local opt-in for research execution.

    Even with sandboxing (P3), running untrusted judge code carries residual
    risk.  This gate remains as an explicit acknowledgement from the operator.
    """
    return os.environ.get("QWENPAW_UNSAFE_RESEARCH") == "1"


def _write_sandbox_runner(sandbox_dir: Path) -> Path:
    """Write a tiny sandbox wrapper that sets resource limits and exec's the judge.

    This avoids :func:`subprocess.run`\'s ``preexec_fn`` which is unsafe in
    multi-threaded programs (the child inherits parent lock state after fork).
    Instead, the runner sets limits on *itself* before exec, which is safe.

    Returns the path to the runner script.
    """
    runner = sandbox_dir / "_runner.py"
    runner.write_text(
        "import os, sys\n"
        "try:\n"
        "    import resource as _r\n"
        "except ImportError:\n"
        "    _r = None  # Windows / non-posix: no rlimits, just exec\n"
        "if _r is not None:\n"
        "    mb = 1024 * 1024\n"
        f"    _r.setrlimit(_r.RLIMIT_CPU, ({_SANDBOX_CPU_HARD}, {_SANDBOX_CPU_HARD}))\n"
        f"    _r.setrlimit(_r.RLIMIT_FSIZE, ({_SANDBOX_FSIZE_MB} * mb, {_SANDBOX_FSIZE_MB} * mb))\n"
        "    for _lim, _mb in [(_r.RLIMIT_AS, 512), (_r.RLIMIT_DATA, 256)]:\n"
        "        try: _r.setrlimit(_lim, (_mb * mb, _mb * mb))\n"
        "        except (ValueError, OSError): pass\n"
        "os.execv(sys.executable, [sys.executable] + sys.argv[1:])\n",
        encoding="utf-8",
    )
    runner.chmod(0o500)
    return runner


def _read_output_file(path: Path) -> str:
    """Read a sandbox output file, capped at ``_MAX_READ_BYTES`` (64 KiB).

    Contract:
        - Parent-side: never returns more than 64 KiB (cross-platform guarantee).
        - Unix child: RLIMIT_FSIZE (10 MiB) set by the runner — best-effort.
        - Windows child: no filesize limit; residual disk-fill risk accepted.

    Oversized files (st_size > ``_MAX_READ_BYTES``) return empty string."""
    try:
        st = path.stat()
        if st.st_size > _MAX_READ_BYTES:
            return ""  # treat oversized output as empty (judge misbehaving)
        raw = path.read_bytes()
        return raw[:_MAX_READ_BYTES].decode("utf-8", errors="replace")
    except (OSError, UnicodeDecodeError):
        return ""


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
        sandbox = Path(temporary_dir)
        candidate = sandbox / task.solution.name
        try:
            _atomic_write(candidate, source)
        except OSError as exc:
            return Evaluation(False, float("-inf"), {}, f"candidate write failed: {exc}")
        candidate.chmod(0o700)

        # P3: Copy judge into sandbox so it cannot access task.root.
        sandbox_judge = sandbox / "judge.py"
        shutil.copy2(task.judge, sandbox_judge)
        sandbox_judge.chmod(0o500)

        # P3: Write sandbox runner that sets rlimits and exec's the judge.
        # This avoids preexec_fn which is unsafe in multi-threaded programs.
        runner = _write_sandbox_runner(sandbox) if _SUPPORTS_RLIMITS else None

        interpreter = sys.executable
        # P3: Redirect stdout/stderr to files to protect parent memory.
        stdout_path = sandbox / "stdout.txt"
        stderr_path = sandbox / "stderr.txt"
        for attempt in range(2):
            try:
                if runner is not None:
                    command = [interpreter, str(runner), str(sandbox_judge), str(candidate)]
                else:
                    command = [interpreter, str(sandbox_judge), str(candidate)]

                with open(stdout_path, "w", encoding="utf-8") as stdout_file, \
                     open(stderr_path, "w", encoding="utf-8") as stderr_file:
                    completed = subprocess.run(
                        command,
                        cwd=sandbox,
                        text=True,
                        stdin=subprocess.DEVNULL,
                        stdout=stdout_file,
                        stderr=stderr_file,
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

        # P3: Read output files before sandbox is cleaned up.
        stdout_text = _read_output_file(stdout_path)
        stderr_text = _read_output_file(stderr_path)

    if completed.returncode != 0:
        error = stderr_text.strip() or stdout_text.strip()
        return Evaluation(False, float("-inf"), {}, error[-2000:] or "judge failed")
    try:
        payload = json.loads(stdout_text)
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


# ── P1: Four-Phase Pipeline ──────────────────────────────────────────────


def _build_plan_prompt(
    task: ResearchTask,
    source: str,
    outcomes: list[ResearchOutcome],
) -> str:
    """Build a prompt for the PLAN phase — ask for a structured solution plan."""
    feedback = "\n".join(
        f"round={item.round} status={item.status} "
        f"score={item.candidate_score} error={item.error}"
        for item in outcomes[-5:]
    )
    language = task.solution.suffix.removeprefix(".")
    return (
        task.program.read_text(encoding="utf-8").strip()
        + "\n\nProduce a concise solution plan. Describe your approach in bullet "
        + "points covering: (1) core algorithm, (2) edge cases, (3) complexity. "
        + "Do NOT emit code yet — only the plan."
        + f"\n\nCURRENT BEST\n```{language}\n{source}```"
        + "\n\nRECENT EVALUATOR FEEDBACK\n"
        + (feedback or "none")
        + "\n"
    )


def _build_program_prompt(
    task: ResearchTask,
    source: str,
    outcomes: list[ResearchOutcome],
    plan: str,
) -> str:
    """Build a prompt for the PROGRAM phase — ask for code based on the plan."""
    feedback = "\n".join(
        f"round={item.round} status={item.status} "
        f"score={item.candidate_score} error={item.error}"
        for item in outcomes[-5:]
    )
    language = task.solution.suffix.removeprefix(".")
    return (
        task.program.read_text(encoding="utf-8").strip()
        + "\n\nReturn only the complete candidate source. Do not use tools or edit files."
        + f"\n\nSOLUTION PLAN\n{plan}"
        + f"\n\nCURRENT BEST\n```{language}\n{source}```"
        + "\n\nRECENT EVALUATOR FEEDBACK\n"
        + (feedback or "none")
        + "\n"
    )


async def _phase_plan(
    proposer: Callable[[str], Awaitable[str]],
    task: ResearchTask,
    source: str,
    outcomes: list[ResearchOutcome],
    on_progress: Callable[[tuple[int, str, str]], None] | None,
    round_number: int,
) -> str:
    """PLAN phase: AI analyzes the problem and produces a structured solution plan.

    Retries up to _PLAN_MAX_ATTEMPTS times if the plan is empty or too short.
    Returns the plan text.
    """
    for attempt in range(_PLAN_MAX_ATTEMPTS):
        if on_progress is not None:
            label = "planning" if attempt == 0 else "planning_retry"
            attempt_label = "" if attempt == 0 else f" (重试 {attempt}/{_PLAN_MAX_ATTEMPTS - 1})"
            on_progress((
                round_number,
                label,
                f"AI 分析问题并制定方案 (第 {round_number} 轮){attempt_label}",
            ))
        prompt = _build_plan_prompt(task, source, outcomes)
        response = await proposer(prompt)
        plan = response.strip()
        if not plan:
            if attempt < _PLAN_MAX_ATTEMPTS - 1:
                continue
            raise ValueError("plan phase exhausted: empty plan after all attempts")
        # Accept plan — no format validation beyond non-empty
        return plan
    raise ValueError("plan phase failed")  # unreachable


async def _phase_program(
    proposer: Callable[[str], Awaitable[str]],
    task: ResearchTask,
    source: str,
    outcomes: list[ResearchOutcome],
    plan: str,
    on_progress: Callable[[tuple[int, str, str]], None] | None,
    round_number: int,
) -> str:
    """PROGRAM phase: AI writes candidate code based on the plan.

    First attempt: generate from plan.
    Subsequent attempts: repair with specific preflight error feedback.
    Returns the candidate source code.
    """
    # First attempt: generate
    if on_progress is not None:
        on_progress((
            round_number,
            "programming",
            f"AI 基于方案编写代码 (第 {round_number} 轮)",
        ))
    prompt = _build_program_prompt(task, source, outcomes, plan)
    response = await proposer(prompt)
    candidate = _extract_candidate(response)

    # Preflight + repair loop
    for repair in range(_PROGRAM_MAX_ATTEMPTS):  # 0 = first try, 1+ = repairs
        valid, preflight_error = _preflight_candidate(candidate)
        if valid:
            break
        if repair < _PROGRAM_MAX_ATTEMPTS - 1:
            if on_progress is not None:
                on_progress((
                    round_number,
                    "preflight_repair",
                    f"候选代码预检失败，请求修复 ({repair + 1}/{_PROGRAM_MAX_ATTEMPTS - 1}): {preflight_error[:100]}",
                ))
            repair_prompt = (
                _build_program_prompt(task, source, outcomes, plan)
                + f"\n\n[SYSTEM] 上次生成的代码无法通过预检: {preflight_error}\n"
                "请修复语法问题和异常 Unicode 字符后重新生成代码。\n"
                "确保代码直接放入 ```python 代码块中，使用标准 ASCII 引号和缩进。"
            )
            repair_response = await proposer(repair_prompt)
            candidate = _extract_candidate(repair_response)
        else:
            raise ValueError(
                f"program phase exhausted: preflight failed after "
                f"{_PROGRAM_MAX_ATTEMPTS - 1} repairs: {preflight_error}"
            )

    # Send proposal summary event
    proposal_preview = _proposal_summary(response, source, candidate)
    if on_progress is not None:
        on_progress((round_number, "proposal", proposal_preview))

    return candidate


async def _phase_judge(
    task: ResearchTask,
    candidate: str,
    judge_timeout: float,
    on_progress: Callable[[tuple[int, str, str]], None] | None,
    round_number: int,
) -> Evaluation:
    """JUDGE phase: execute the existing evaluator (judge.py) against the candidate.

    Retries once on timeout. JUDGE is deterministic — no AI involved.
    """
    for attempt in range(_JUDGE_MAX_ATTEMPTS):
        if on_progress is not None:
            label = "judging" if attempt == 0 else "judging_retry"
            attempt_label = "" if attempt == 0 else f" (重试 {attempt}/{_JUDGE_MAX_ATTEMPTS - 1})"
            on_progress((
                round_number,
                label,
                f"评测第 {round_number} 轮候选方案{attempt_label}",
            ))
        evaluation = await asyncio.to_thread(
            evaluate_candidate,
            task,
            candidate,
            judge_timeout,
        )
        # Only retry on transient failures (timeout), not on failing scores
        if evaluation.error != "judge timed out":
            return evaluation
    return evaluation  # return last result after all attempts


def _phase_review(
    evaluation: Evaluation,
    baseline: Evaluation,
    source: str,
    candidate: str,
    min_improvement: float,
    round_number: int,
) -> ResearchOutcome:
    """REVIEW phase: determine whether to keep or reject the candidate.

    Pure deterministic logic — no AI, no retry.
    """
    improvement = evaluation.score - baseline.score if evaluation.passed else 0.0
    status = (
        "kept"
        if evaluation.passed and improvement > min_improvement
        else "rejected"
    )
    return ResearchOutcome(
        round_number,
        status,
        evaluation.passed,
        baseline.score,
        evaluation.score if evaluation.passed else None,
        improvement,
        evaluation.metrics,
        evaluation.error,
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


def _fsync_directory(directory: Path) -> None:
    """Best-effort fsync of a directory to improve crash durability.

    Not all platforms support directory fsync; failures are silently ignored.
    """
    try:
        fd = os.open(str(directory), os.O_RDONLY)
    except (OSError, AttributeError):
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def _write_all(fd: int, data: bytes) -> None:
    """Write all bytes of *data* to file descriptor *fd*, handling partial writes.

    :func:`os.write` may write fewer bytes than requested. This helper loops
    until all bytes are written or raises :exc:`OSError` if progress stops.
    """
    view = memoryview(data)
    offset = 0
    while offset < len(view):
        written = os.write(fd, view[offset:])
        if written <= 0:
            raise OSError("os.write made no progress")
        offset += written


def _atomic_write(path: Path, content: str) -> None:
    """Write *content* to *path* atomically with integrity verification.

    Guarantees:
    * The target file is never observed in a partially-written state.
    * On success the content on disk matches *content* byte-for-byte.
    * On failure the original file (if any) is untouched and the temporary
      file is cleaned up.

    Raises :exc:`OSError` when write, sync, verification, or replace fails.
    """
    expected = content.encode("utf-8")
    fd: int = -1
    temp_path: str | None = None

    try:
        fd, temp_path = tempfile.mkstemp(
            dir=path.parent,
            prefix=path.name + ".",
            suffix=".tmp",
        )
        _write_all(fd, expected)
        os.fsync(fd)
    except OSError as exc:
        _cleanup_temp(temp_path)
        raise OSError(f"atomic write failed for {path}") from exc
    finally:
        if fd >= 0:
            os.close(fd)

    # Integrity: byte-for-byte comparison after close
    try:
        actual = Path(temp_path).read_bytes()
        if actual != expected:
            raise OSError(
                f"temporary file verification failed ({len(actual)} vs {len(expected)} bytes)"
            )
    except OSError:
        _cleanup_temp(temp_path)
        raise

    # Preserve existing permissions when replacing
    if path.exists():
        try:
            existing_mode = stat.S_IMODE(path.stat().st_mode)
            os.chmod(temp_path, existing_mode)
        except OSError:
            _cleanup_temp(temp_path)
            raise

    # Atomic replacement
    try:
        os.replace(temp_path, str(path))
    except OSError as exc:
        _cleanup_temp(temp_path)
        raise OSError(f"atomic write failed for {path}") from exc

    # Best-effort directory sync for crash durability
    _fsync_directory(path.parent)


def _cleanup_temp(temp_path: str | None) -> None:
    """Remove a temporary file, ignoring any errors."""
    if temp_path is not None:
        try:
            Path(temp_path).unlink(missing_ok=True)
        except OSError:
            pass


def _replace_solution(path: Path, source: str) -> None:
    _atomic_write(path, source)


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
    is_cancelled: Callable[[], bool] | None = None,
) -> tuple[ResearchOutcome, ...]:
    """Run a research loop with four-phase pipeline per round.

    Each round: PLAN → PROGRAM → JUDGE → REVIEW.
    Each phase has independent retry limits and progress reporting.

    Provide *is_cancelled* (a synchronous predicate) to enable mid-round
    cancellation: the orchestrator checks it between phases and short-circuits
    the remaining stages with a ``"cancelled"`` outcome.
    """
    if rounds < 1:
        raise ValueError("rounds must be at least 1")
    if min_improvement < 0:
        raise ValueError("min_improvement must be non-negative")
    task = load_task(task_dir, database_path)
    source = task.solution.read_text(encoding="utf-8")
    outcomes: list[ResearchOutcome] = []

    for round_number in range(1, rounds + 1):
        parent_source = source

        # ── Baseline evaluation ──
        if on_progress is not None:
            on_progress((
                round_number,
                "evaluating_baseline",
                f"评测第 {round_number} 轮基线方案",
            ))
        baseline = await asyncio.to_thread(
            evaluate_candidate,
            task,
            source,
            judge_timeout,
        )
        if not baseline.passed:
            if on_progress is not None:
                on_progress((
                    round_number,
                    "baseline_failing",
                    f"基线未通过: {baseline.error[:120]}",
                ))

        try:
            # ── Cancellation check before PLAN ──
            if is_cancelled is not None and is_cancelled():
                raise _CancelledError()

            # ── Phase 1: PLAN ──
            plan = await _phase_plan(
                proposer, task, source, outcomes, on_progress, round_number,
            )

            # ── Cancellation check before PROGRAM ──
            if is_cancelled is not None and is_cancelled():
                raise _CancelledError()

            # ── Phase 2: PROGRAM ──
            candidate = await _phase_program(
                proposer, task, source, outcomes, plan,
                on_progress, round_number,
            )

            # ── Cancellation check before JUDGE ──
            if is_cancelled is not None and is_cancelled():
                raise _CancelledError()

            # ── Phase 3: JUDGE ──
            evaluation = await _phase_judge(
                task, candidate, judge_timeout, on_progress, round_number,
            )

            # ── Cancellation check before REVIEW ──
            if is_cancelled is not None and is_cancelled():
                raise _CancelledError()

            # ── Phase 4: REVIEW ──
            outcome = _phase_review(
                evaluation, baseline, source, candidate,
                min_improvement, round_number,
            )

            # Send rich outcome detail
            if on_progress is not None:
                detail = _outcome_summary(outcome, baseline.score, source, candidate)
                on_progress((round_number, outcome.status, detail))

        except _CancelledError:
            # Run cancelled externally — record as cancelled outcome
            candidate = ""
            outcome = ResearchOutcome(
                round_number,
                "cancelled",
                False,
                baseline.score,
                None,
                0.0,
                {},
                "run cancelled",
            )
            if on_progress is not None:
                on_progress((round_number, "run.cancelled", "运行已被取消"))

        except ValueError as exc:
            # Phase exhausted — record as phase_error outcome
            candidate = ""
            outcome = ResearchOutcome(
                round_number,
                "phase_error",
                False,
                baseline.score,
                None,
                0.0,
                {},
                str(exc),
            )
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
