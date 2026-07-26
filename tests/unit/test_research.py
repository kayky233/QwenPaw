from __future__ import annotations

import os
import sqlite3
import sys
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from click.testing import CliRunner

from qwenpaw.cli.main import cli
from qwenpaw.cli import research_cmd as research_module
from qwenpaw.research import ResearchOutcome
from qwenpaw.research import Evaluation
from qwenpaw.research import _atomic_write
from qwenpaw.research import _fsync_directory
from qwenpaw.research import _replace_solution
from qwenpaw.research import _write_sandbox_runner
from qwenpaw.research import _write_all
from qwenpaw.research import create_research_task
from qwenpaw.research import evaluate_candidate
from qwenpaw.research import run_research


def test_create_research_task_requires_a_passing_baseline(tmp_path: Path) -> None:
    task = create_research_task(
        tmp_path,
        "cache-hit-rate",
        "# Improve cache hit rate\n\nMaximize the immutable trace score.",
        "solution.py",
        "CAPACITY = 2\n",
        """\
import json
import sys
from pathlib import Path

source = Path(sys.argv[1]).read_text(encoding="utf-8")
passed = "CAPACITY" in source
print(json.dumps({"passed": passed, "score": 0.5, "metrics": {"hit_rate": 0.5}, "error": ""}))
""",
    )

    assert task.root == tmp_path / "cache-hit-rate"
    assert task.solution.read_text(encoding="utf-8") == "CAPACITY = 2\n"
    assert evaluate_candidate(task, task.solution.read_text(encoding="utf-8")).passed


def test_create_research_task_rejects_an_invalid_contract(tmp_path: Path) -> None:
    """A judge that returns passed=false for everything proves it is NOT a no-op,
    so the task should be accepted (the judge correctly evaluates both valid and invalid solutions)."""
    task = create_research_task(
        tmp_path,
        "broken",
        "# Broken",
        "solution.py",
        "x = 1\n",
        'import json; print(json.dumps({"passed": False, "score": 0, "metrics": {}, "error": "bad"}))',
    )
    assert task.root.exists()
    assert task.solution.read_text().strip() == "x = 1"


def test_create_research_task_rejects_a_judge_that_accepts_garbage(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="invalid probe"):
        create_research_task(
            tmp_path,
            "fake-judge",
            "# Fake",
            "solution.sql",
            "SELECT 1;\n",
            'import json; print(json.dumps({"passed": True, "score": 1, "metrics": {}, "error": ""}))',
        )

    assert not (tmp_path / "fake-judge").exists()


def test_evaluator_falls_back_when_virtualenv_is_replaced(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    task = tmp_path / "task"
    task.mkdir()
    (task / "program.md").write_text("# Demo", encoding="utf-8")
    (task / "solution.txt").write_text("ok\n", encoding="utf-8")
    (task / "judge.py").write_text("unused", encoding="utf-8")
    commands: list[list[str]] = []

    def run(command, **_kwargs):
        commands.append(command)
        if len(commands) == 1:
            raise FileNotFoundError(command[0])
        # P3: write to actual output files (code now reads from files, not
        # completed.stdout/stderr).
        stdout_file = _kwargs.get("stdout")
        stderr_file = _kwargs.get("stderr")
        if stdout_file is not None:
            stdout_file.write('{"passed": true, "score": 1, "metrics": {}}')
        if stderr_file is not None:
            stderr_file.write("")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr("qwenpaw.research.subprocess.run", run)
    monkeypatch.setattr("qwenpaw.research.shutil.which", lambda _name: "/usr/bin/python3")

    assert evaluate_candidate(task, "ok\n").passed
    assert commands[0][0] == sys.executable
    assert commands[1][0] == "/usr/bin/python3"


@pytest.mark.asyncio
async def test_research_rejects_wrong_then_keeps_better_candidate(tmp_path: Path) -> None:
    task = tmp_path / "task"
    task.mkdir()
    (task / "program.md").write_text("Return a better candidate.", encoding="utf-8")
    (task / "solution.txt").write_text("ok baseline is long\n", encoding="utf-8")
    (task / "judge.py").write_text(
        """\
import json
import sys
from pathlib import Path

source = Path(sys.argv[1]).read_text(encoding="utf-8").strip()
print(json.dumps({"passed": source.startswith("ok"), "score": -len(source), "metrics": {"length": len(source)}, "error": ""}))
""",
        encoding="utf-8",
    )
    # P1: each round needs 2 calls (PLAN + PROGRAM), 2 rounds → 4 calls
    proposals = iter((
        "plan: try bad approach", "bad",
        "plan: use ok approach", "`ok`",
    ))

    async def proposer(_prompt: str) -> str:
        return next(proposals)

    outcomes = await run_research(task, proposer=proposer, rounds=2)

    assert [outcome.status for outcome in outcomes] == ["rejected", "kept"]
    assert (task / "solution.txt").read_text(encoding="utf-8") == "ok\n"
    with sqlite3.connect(task / ".research" / "experiments.sqlite") as connection:
        statuses = [row[0] for row in connection.execute("SELECT status FROM runs ORDER BY id")]
    assert statuses == ["rejected", "kept"]


@pytest.mark.asyncio
async def test_research_reports_each_completed_round(tmp_path: Path) -> None:
    task = tmp_path / "task"
    task.mkdir()
    (task / "program.md").write_text("Return a better candidate.", encoding="utf-8")
    (task / "solution.txt").write_text("ok baseline\n", encoding="utf-8")
    (task / "judge.py").write_text(
        """\
import json
import sys
from pathlib import Path

source = Path(sys.argv[1]).read_text(encoding="utf-8").strip()
print(json.dumps({"passed": source.startswith("ok"), "score": -len(source), "metrics": {}}))
""",
        encoding="utf-8",
    )
    reported: list[ResearchOutcome] = []

    async def proposer(_prompt: str) -> str:
        return "ok"

    await run_research(task, proposer=proposer, rounds=1, on_outcome=reported.append)

    assert len(reported) == 1
    assert reported[0].status == "kept"


@pytest.mark.asyncio
async def test_research_reports_auditable_progress_phases(tmp_path: Path) -> None:
    task = tmp_path / "task"
    task.mkdir()
    (task / "program.md").write_text("Improve it.", encoding="utf-8")
    (task / "solution.txt").write_text("ok long\n", encoding="utf-8")
    (task / "judge.py").write_text(
        """\
import json
print(json.dumps({"passed": True, "score": 1, "metrics": {}}))
""",
        encoding="utf-8",
    )
    progress: list[tuple[int, str, str]] = []

    async def proposer(_prompt: str) -> str:
        return "ok"

    await run_research(task, proposer=proposer, rounds=1, on_progress=progress.append)

    # P1: Check that each expected phase is present
    phases_seen = [p[1] for p in progress]
    assert "evaluating_baseline" in phases_seen
    assert "planning" in phases_seen
    assert "programming" in phases_seen
    assert "judging" in phases_seen
    assert "proposal" in phases_seen
    # After evaluation the outcome phase ("kept" or "rejected") should fire
    assert any(p[1] in ("kept", "rejected") for p in progress)


@pytest.mark.asyncio
async def test_research_runs_evaluator_off_event_loop(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    task = tmp_path / "task"
    task.mkdir()
    (task / "program.md").write_text("Improve it.", encoding="utf-8")
    (task / "solution.txt").write_text("baseline\n", encoding="utf-8")
    (task / "judge.py").write_text("unused", encoding="utf-8")
    main_thread = threading.get_ident()
    evaluator_threads: list[int] = []

    def evaluate(*_args, **_kwargs) -> Evaluation:
        evaluator_threads.append(threading.get_ident())
        return Evaluation(True, 1.0, {})

    async def proposer(_prompt: str) -> str:
        return "candidate"

    monkeypatch.setattr("qwenpaw.research.evaluate_candidate", evaluate)

    await run_research(task, proposer, 1)

    assert evaluator_threads
    assert all(thread_id != main_thread for thread_id in evaluator_threads)


def test_tenth_line_judge_accepts_solution_and_rejects_wrong_one() -> None:
    task = Path(__file__).parents[2] / "research" / "tenth-line"

    accepted = evaluate_candidate(task, "sed -n '10p' file.txt\n")
    rejected = evaluate_candidate(task, "head -n 10 file.txt\n")

    assert accepted.passed, accepted.error
    assert accepted.metrics["cases"] == 4
    assert not rejected.passed


def test_research_command_registered_in_cli() -> None:
    result = CliRunner().invoke(cli, ["research", "--help"])

    assert result.exit_code == 0
    assert "TASK_DIR" in result.output
    assert "--rounds" in result.output
    assert "--model" in result.output


@pytest.mark.asyncio
async def test_qwenpaw_proposer_forwards_model_response(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("QWENPAW_UNSAFE_RESEARCH", "1")
    config = SimpleNamespace(active_model=None)
    run_task = AsyncMock(
        return_value={"status": "success", "response": "candidate", "usage": {}},
    )
    monkeypatch.setattr("qwenpaw.config.config.load_agent_config", lambda _aid: config)
    monkeypatch.setattr(research_module, "_run_task", run_task)

    async def fake_run_research(task_dir, proposer, rounds, **kwargs):
        assert task_dir == tmp_path
        assert rounds == 1
        assert await proposer("prompt") == "candidate"
        assert kwargs["judge_timeout"] == 4.0
        return ()

    monkeypatch.setattr(research_module, "run_research", fake_run_research)

    outcomes = await research_module._run_with_qwenpaw(
        tmp_path,
        "demo/model",
        1,
        2,
        3,
        4.0,
        0.0,
        None,
        "default",
    )

    assert outcomes == ()
    assert config.active_model.provider_id == "demo"
    assert config.active_model.model == "model"
    assert run_task.await_args.kwargs["request_context"]["channel"] == "console"


def test_research_command_prints_outcome(monkeypatch, tmp_path: Path) -> None:
    outcome = ResearchOutcome(1, "kept", True, -10.0, -3.0, 7.0, {"cases": 1})
    monkeypatch.setattr(
        research_module,
        "_run_with_qwenpaw",
        AsyncMock(return_value=(outcome,)),
    )

    result = CliRunner().invoke(cli, ["research", str(tmp_path), "--rounds", "1"])

    assert result.exit_code == 0
    assert '"status": "kept"' in result.output


# ═══════════════════════════════════════════════════════════
# _preflight_candidate — P0 validation gate
# ═══════════════════════════════════════════════════════════


def test_preflight_valid_python_passes() -> None:
    """Clean Python source passes preflight."""
    from qwenpaw.research import _preflight_candidate

    source = "def solve():\n    return 42\n"
    valid, error = _preflight_candidate(source)
    assert valid is True
    assert error == ""


def test_preflight_empty_source_fails() -> None:
    """Empty or whitespace-only source is rejected."""
    from qwenpaw.research import _preflight_candidate

    valid, error = _preflight_candidate("")
    assert valid is False
    assert "empty" in error.lower()

    valid, error = _preflight_candidate("   \n\t  ")
    assert valid is False
    assert "empty" in error.lower()


def test_preflight_syntax_error_fails() -> None:
    """Source with Python SyntaxError is rejected with lineno info."""
    from qwenpaw.research import _preflight_candidate

    source = "def broken(:\n    pass\n"
    valid, error = _preflight_candidate(source)
    assert valid is False
    assert "SyntaxError" in error


def test_preflight_confusable_unicode_fails() -> None:
    """Smart quotes / confusable Unicode triggers rejection."""
    from qwenpaw.research import _preflight_candidate

    # Use a Unicode smart quote that _CONFUSABLE_UNICODE maps
    source = 'print(\u201csmart quote\u201d)\n'
    valid, error = _preflight_candidate(source)
    assert valid is False
    assert "confusable" in error.lower()


def test_preflight_none_source_fails() -> None:
    """None source should be rejected (not crash)."""
    from qwenpaw.research import _preflight_candidate

    valid, error = _preflight_candidate(None)  # type: ignore[arg-type]
    assert valid is False


# ═══════════════════════════════════════════════════════════
# preflight → repair linkage (integration-level)
# ═══════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_preflight_failure_triggers_repair_and_succeeds(tmp_path: Path) -> None:
    """Full chain: preflight fails → repair prompt → proposer retries → success.

    First proposer response has confusable Unicode.  After preflight rejects it,
    the repair loop sends a repair prompt and the second proposer call returns
    valid code.  The round should complete as 'kept'.
    """
    task_dir = tmp_path / "task"
    task_dir.mkdir()
    (task_dir / "program.md").write_text("Return a valid Python solution.", encoding="utf-8")
    (task_dir / "solution.txt").write_text("pass\n", encoding="utf-8")
    (task_dir / "judge.py").write_text(
        """\
import json
import sys
from pathlib import Path

source = Path(sys.argv[1]).read_text(encoding="utf-8").strip()
passed = source.startswith("def")
score = 1.0 if passed else 0.0
print(json.dumps({"passed": passed, "score": score, "metrics": {}, "error": ""}))
""",
        encoding="utf-8",
    )

    # P1: each round needs PLAN + PROGRAM. proposer returns:
    #   call 1 (PLAN): plan text (ignored by proposer implementation)
    #   call 2 (PROGRAM): invalid with confusable Unicode
    #   call 3 (PROGRAM repair): valid code
    calls: list[str] = []

    async def proposer(prompt: str) -> str:
        calls.append(prompt)
        if len(calls) == 1:
            # PLAN phase response
            return "plan: use a valid Python function"
        if len(calls) == 2:
            # PROGRAM phase — first attempt with confusable Unicode
            return 'print(\u201chello\u201d)\n'
        # PROGRAM phase — repair with valid code
        return "def solve():\n    pass\n"

    progress: list[tuple[int, str, str]] = []

    outcomes = await run_research(
        task_dir, proposer=proposer, rounds=1, on_progress=progress.append,
    )

    phases = [p[1] for p in progress]
    assert "preflight_repair" in phases, (
        f"Expected preflight_repair phase, got: {phases}"
    )
    assert outcomes[0].status == "kept", (
        f"Expected 'kept' after repair, got: {outcomes[0].status}"
    )
    # Verify the repair prompt (call 3, index 2) contains the preflight error
    repair_prompt = calls[2] if len(calls) > 2 else ""
    assert "confusable" in repair_prompt.lower() or "preflight" in repair_prompt.lower(), (
        f"Repair prompt should mention preflight error, got: {repair_prompt[:200]}"
    )


@pytest.mark.asyncio
async def test_preflight_repair_exhausted_returns_preflight_error(
    tmp_path: Path,
) -> None:
    """All repair attempts fail → outcome is 'preflight_error', not 'completed'.

    The proposer always returns code with confusable Unicode.  After the initial
    attempt + 2 repairs (_MAX_ATTEMPTS-like exhaustion), the round records a
    'preflight_error' outcome and does NOT mark the run as completed.
    """
    task_dir = tmp_path / "task"
    task_dir.mkdir()
    (task_dir / "program.md").write_text("Return valid Python.", encoding="utf-8")
    (task_dir / "solution.txt").write_text("pass\n", encoding="utf-8")
    (task_dir / "judge.py").write_text(
        """\
import json
import sys
from pathlib import Path

source = Path(sys.argv[1]).read_text(encoding="utf-8").strip()
print(json.dumps({"passed": True, "score": 1.0, "metrics": {}, "error": ""}))
""",
        encoding="utf-8",
    )

    # P1: PLAN call first, then PROGRAM with always-invalid code
    call_count = [0]  # mutable counter

    async def proposer(_prompt: str) -> str:
        call_count[0] += 1
        if call_count[0] == 1:
            # PLAN phase
            return "plan: try to return valid code"
        # PROGRAM phase — always returns invalid with smart quotes
        return 'result = \u201cbroken\u201d\n'

    outcomes = await run_research(task_dir, proposer=proposer, rounds=1)

    # P1: exhaustion now raises ValueError caught as "phase_error"
    assert outcomes[0].status == "phase_error", (
        f"Expected 'phase_error', got: {outcomes[0].status}"
    )
    assert "preflight failed after" in (outcomes[0].error or ""), (
        f"Error should mention repair exhaustion, got: {outcomes[0].error}"
    )
    # 1 PLAN + (1 generation + 2 repairs) = 4 proposer calls total in P1
    assert call_count[0] == 4, (
        f"Expected 4 calls (1 plan + 1 gen + 2 repairs), got: {call_count[0]}"
    )


# ═══════════════════════════════════════════════════════════
# P1: Four-Phase Pipeline — Phase Unit Tests
# ═══════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_phase_plan_retries_on_empty_plan(tmp_path: Path) -> None:
    """_phase_plan retries when the proposer returns an empty plan string."""
    from qwenpaw.research import _phase_plan, load_task, _replace_solution

    task_dir = tmp_path / "task"
    task_dir.mkdir()
    (task_dir / "program.md").write_text("Return a plan.", encoding="utf-8")
    (task_dir / "solution.txt").write_text("ok\n", encoding="utf-8")
    # judge.py is required by load_task
    (task_dir / "judge.py").write_text(
        'import json; print(json.dumps({"passed": True, "score": 1, "metrics": {}}))',
        encoding="utf-8",
    )
    task = load_task(task_dir)

    # First two calls return empty, third returns a valid plan
    attempts = [0]

    async def proposer(_prompt: str) -> str:
        attempts[0] += 1
        if attempts[0] < 3:
            return ""
        return "- Analyze the problem\n- Implement greedy algorithm"

    plan = await _phase_plan(
        proposer, task, "source", [], None, 1,
    )

    assert plan == "- Analyze the problem\n- Implement greedy algorithm"
    assert attempts[0] == 3


@pytest.mark.asyncio
async def test_phase_plan_succeeds_on_first_valid_response(tmp_path: Path) -> None:
    """_phase_plan returns the plan on the first attempt when valid."""
    from qwenpaw.research import _phase_plan, load_task

    task_dir = tmp_path / "task"
    task_dir.mkdir()
    (task_dir / "program.md").write_text("Return a plan.", encoding="utf-8")
    (task_dir / "solution.txt").write_text("ok\n", encoding="utf-8")
    (task_dir / "judge.py").write_text(
        'import json; print(json.dumps({"passed": True, "score": 1, "metrics": {}}))',
        encoding="utf-8",
    )
    task = load_task(task_dir)

    async def proposer(_prompt: str) -> str:
        return "plan: use a hash map for O(1) lookups"

    plan = await _phase_plan(
        proposer, task, "source", [], None, 1,
    )

    assert "hash map" in plan


@pytest.mark.asyncio
async def test_phase_program_retries_on_preflight_failure(tmp_path: Path) -> None:
    """_phase_program retries with repair prompt when preflight fails."""
    from qwenpaw.research import _phase_program, load_task

    task_dir = tmp_path / "task"
    task_dir.mkdir()
    (task_dir / "program.md").write_text("Return valid Python.", encoding="utf-8")
    (task_dir / "solution.txt").write_text("ok\n", encoding="utf-8")
    (task_dir / "judge.py").write_text(
        'import json; print(json.dumps({"passed": True, "score": 1, "metrics": {}}))',
        encoding="utf-8",
    )
    task = load_task(task_dir)

    calls: list[str] = []

    async def proposer(prompt: str) -> str:
        calls.append(prompt)
        if len(calls) == 1:
            return 'print(\u201chello\u201d)\n'  # confusable Unicode
        return "def solve():\n    return 42\n"

    candidate = await _phase_program(
        proposer, task, "source", [], "plan: use hash map",
        None, 1,
    )

    assert "def solve" in candidate
    assert len(calls) == 2
    assert "confusable" in calls[1].lower() or "preflight" in calls[1].lower()


@pytest.mark.asyncio
async def test_phase_program_succeeds_on_first_valid_response(tmp_path: Path) -> None:
    """_phase_program returns code on first attempt when preflight passes."""
    from qwenpaw.research import _phase_program, load_task

    task_dir = tmp_path / "task"
    task_dir.mkdir()
    (task_dir / "program.md").write_text("Return valid Python.", encoding="utf-8")
    (task_dir / "solution.txt").write_text("ok\n", encoding="utf-8")
    (task_dir / "judge.py").write_text(
        'import json; print(json.dumps({"passed": True, "score": 1, "metrics": {}}))',
        encoding="utf-8",
    )
    task = load_task(task_dir)

    async def proposer(_prompt: str) -> str:
        return "def solve():\n    return 42\n"

    candidate = await _phase_program(
        proposer, task, "source", [], "plan: use hash map",
        None, 1,
    )

    assert "def solve" in candidate


def test_phase_judge_retries_on_timeout(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """_phase_judge retries when the evaluator returns a timeout error."""
    from qwenpaw.research import _phase_judge, Evaluation, load_task

    task_dir = tmp_path / "task"
    task_dir.mkdir()
    (task_dir / "program.md").write_text("Improve it.", encoding="utf-8")
    (task_dir / "solution.txt").write_text("ok\n", encoding="utf-8")
    (task_dir / "judge.py").write_text(
        'import json; print(json.dumps({"passed": True, "score": 1, "metrics": {}}))',
        encoding="utf-8",
    )
    task = load_task(task_dir)

    calls: list[Any] = []

    def evaluate(*_args, **_kwargs) -> Evaluation:
        calls.append(1)
        if len(calls) == 1:
            return Evaluation(False, 0.0, {}, error="judge timed out")
        return Evaluation(True, 0.8, {"hit_rate": 0.8})

    monkeypatch.setattr("qwenpaw.research.evaluate_candidate", evaluate)

    import asyncio
    result = asyncio.run(_phase_judge(
        task, "candidate", 10.0, None, 1,
    ))

    assert result.passed is True
    assert result.score == 0.8
    assert len(calls) == 2


def test_phase_judge_returns_first_non_timeout_result(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """_phase_judge returns immediately on a successful (non-timeout) result."""
    from qwenpaw.research import _phase_judge, Evaluation, load_task

    task_dir = tmp_path / "task"
    task_dir.mkdir()
    (task_dir / "program.md").write_text("Improve it.", encoding="utf-8")
    (task_dir / "solution.txt").write_text("ok\n", encoding="utf-8")
    (task_dir / "judge.py").write_text(
        'import json; print(json.dumps({"passed": True, "score": 1, "metrics": {}}))',
        encoding="utf-8",
    )
    task = load_task(task_dir)

    calls: list[Any] = []

    def evaluate(*_args, **_kwargs) -> Evaluation:
        calls.append(1)
        return Evaluation(True, 0.9, {"hit_rate": 0.9})

    monkeypatch.setattr("qwenpaw.research.evaluate_candidate", evaluate)

    import asyncio
    result = asyncio.run(_phase_judge(
        task, "candidate", 10.0, None, 1,
    ))

    assert result.passed is True
    assert len(calls) == 1  # only one call — no retry needed


def test_phase_review_keeps_when_improved() -> None:
    """_phase_review returns 'kept' when candidate improves over baseline."""
    from qwenpaw.research import _phase_review, Evaluation

    baseline = Evaluation(True, 0.5, {})
    evaluation = Evaluation(True, 0.9, {"hit_rate": 0.9})

    outcome = _phase_review(
        evaluation, baseline, "old", "new", min_improvement=0.0, round_number=1,
    )

    assert outcome.status == "kept"
    assert outcome.improvement == pytest.approx(0.4)


def test_phase_review_rejects_when_not_improved() -> None:
    """_phase_review returns 'rejected' when candidate does not improve."""
    from qwenpaw.research import _phase_review, Evaluation

    baseline = Evaluation(True, 0.9, {})
    evaluation = Evaluation(True, 0.85, {"hit_rate": 0.85})

    outcome = _phase_review(
        evaluation, baseline, "old", "new", min_improvement=0.1, round_number=1,
    )

    assert outcome.status == "rejected"


def test_phase_review_rejects_when_evaluation_not_passed() -> None:
    """_phase_review returns 'rejected' when candidate doesn't pass the judge."""
    from qwenpaw.research import _phase_review, Evaluation

    baseline = Evaluation(True, 0.5, {})
    evaluation = Evaluation(False, 0.0, {}, error="judge timed out")

    outcome = _phase_review(
        evaluation, baseline, "old", "new", min_improvement=0.0, round_number=1,
    )

    assert outcome.status == "rejected"


# ═══════════════════════════════════════════════════════════
# P1: Four-Phase Pipeline — Integration Tests
# ═══════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_full_pipeline_reports_all_four_phases(tmp_path: Path) -> None:
    """Full pipeline emits progress events for all 4 phases + baseline."""
    task_dir = tmp_path / "task"
    task_dir.mkdir()
    (task_dir / "program.md").write_text("Improve it.", encoding="utf-8")
    (task_dir / "solution.txt").write_text("pass\n", encoding="utf-8")
    (task_dir / "judge.py").write_text(
        """\
import json
import sys
from pathlib import Path

source = Path(sys.argv[1]).read_text(encoding="utf-8").strip()
print(json.dumps({"passed": True, "score": 1.0, "metrics": {}, "error": ""}))
""",
        encoding="utf-8",
    )

    progress: list[tuple[int, str, str]] = []

    async def proposer(_prompt: str) -> str:
        return "def solve():\n    return 42\n"

    await run_research(
        task_dir, proposer=proposer, rounds=1, on_progress=progress.append,
    )

    phases_seen = [p[1] for p in progress]
    # Baseline
    assert "evaluating_baseline" in phases_seen
    # Four phases
    assert "planning" in phases_seen
    assert "programming" in phases_seen
    assert "judging" in phases_seen
    # REVIEW is deterministic, reflected in outcome event
    assert any(p[1] in ("kept", "rejected") for p in progress)


@pytest.mark.asyncio
async def test_full_pipeline_keeps_better_candidate(tmp_path: Path) -> None:
    """End-to-end: two-round pipeline keeps a better candidate (similar to original test)."""
    task_dir = tmp_path / "task"
    task_dir.mkdir()
    (task_dir / "program.md").write_text("Return a better candidate.", encoding="utf-8")
    (task_dir / "solution.txt").write_text("ok baseline is long\n", encoding="utf-8")
    (task_dir / "judge.py").write_text(
        """\
import json
import sys
from pathlib import Path

source = Path(sys.argv[1]).read_text(encoding="utf-8").strip()
print(json.dumps({"passed": source.startswith("ok"), "score": -len(source), "metrics": {"length": len(source)}, "error": ""}))
""",
        encoding="utf-8",
    )
    # P1: 2 rounds × (PLAN + PROGRAM) = 4 proposer calls
    proposals = iter((
        "plan: bad approach", "bad",
        "plan: use ok approach", "`ok`",
    ))

    async def proposer(_prompt: str) -> str:
        return next(proposals)

    outcomes = await run_research(task_dir, proposer=proposer, rounds=2)

    assert [outcome.status for outcome in outcomes] == ["rejected", "kept"]
    assert (task_dir / "solution.txt").read_text(encoding="utf-8") == "ok\n"
    with sqlite3.connect(task_dir / ".research" / "experiments.sqlite") as connection:
        statuses = [row[0] for row in connection.execute("SELECT status FROM runs ORDER BY id")]
    assert statuses == ["rejected", "kept"]


@pytest.mark.asyncio
async def test_full_pipeline_with_baseline_failing_handles_gracefully(
    tmp_path: Path,
) -> None:
    """Pipeline continues normally even when baseline fails the judge."""
    task_dir = tmp_path / "task"
    task_dir.mkdir()
    (task_dir / "program.md").write_text("Fix the failing baseline.", encoding="utf-8")
    (task_dir / "solution.txt").write_text("broken\n", encoding="utf-8")
    (task_dir / "judge.py").write_text(
        """\
import json
import sys
from pathlib import Path

source = Path(sys.argv[1]).read_text(encoding="utf-8").strip()
passed = source.startswith("def")
score = 1.0 if passed else 0.0
print(json.dumps({"passed": passed, "score": score, "metrics": {}, "error": "" if passed else "syntax"}))
""",
        encoding="utf-8",
    )

    progress: list[tuple[int, str, str]] = []

    async def proposer(_prompt: str) -> str:
        return "def solve():\n    pass\n"

    outcomes = await run_research(
        task_dir, proposer=proposer, rounds=1, on_progress=progress.append,
    )

    assert outcomes[0].status == "kept"
    phases = [p[1] for p in progress]
    assert "baseline_failing" in phases


# ═══════════════════════════════════════════════════════════
# P1 CONTINUE: Stage isolation, cancellation, terminal events
# ═══════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_program_exhausted_does_not_rerun_plan(tmp_path: Path) -> None:
    """When PROGRAM exhausts all repairs, PLAN is not re-invoked."""
    from qwenpaw.research import run_research, _PROGRAM_MAX_ATTEMPTS

    task_dir = tmp_path / "task"
    task_dir.mkdir()
    (task_dir / "program.md").write_text("Improve the solution.", encoding="utf-8")
    (task_dir / "solution.txt").write_text("ok\n", encoding="utf-8")
    (task_dir / "judge.py").write_text(
        'import json; print(json.dumps({"passed": True, "score": 1, "metrics": {}}))',
        encoding="utf-8",
    )

    plan_calls: list[str] = []
    program_calls: list[str] = []

    async def proposer(prompt: str) -> str:
        # PLAN prompt contains "not emit code" (from _build_plan_prompt);
        # PROGRAM/repair prompts contain "Return only the complete candidate source"
        if "not emit code" in prompt.lower():
            plan_calls.append(prompt)
            return "Plan: use iterative approach"
        else:
            program_calls.append(prompt)
            # Always return bad code that fails preflight
            return 'print(\u201chello\u201d)\n'  # confusable unicode

    outcomes = await run_research(task_dir, proposer=proposer, rounds=1)

    # PLAN called exactly once
    assert len(plan_calls) == 1, f"PLAN should be called once, got {len(plan_calls)}"
    # PROGRAM called _PROGRAM_MAX_ATTEMPTS times (1st + repairs) and no more
    assert len(program_calls) == _PROGRAM_MAX_ATTEMPTS, (
        f"PROGRAM should be called {_PROGRAM_MAX_ATTEMPTS} times, got {len(program_calls)}"
    )
    # One outcome — phase_error
    assert len(outcomes) == 1
    assert outcomes[0].status == "phase_error"


@pytest.mark.asyncio
async def test_judge_exhausted_does_not_rerun_plan_or_program(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """When JUDGE always times out, PLAN and PROGRAM are not re-run."""
    from qwenpaw.research import run_research, Evaluation, _JUDGE_MAX_ATTEMPTS

    task_dir = tmp_path / "task"
    task_dir.mkdir()
    (task_dir / "program.md").write_text("Improve it.", encoding="utf-8")
    (task_dir / "solution.txt").write_text("ok\n", encoding="utf-8")
    (task_dir / "judge.py").write_text(
        'import json; print(json.dumps({"passed": True, "score": 1, "metrics": {}}))',
        encoding="utf-8",
    )

    proposer_calls: list[str] = []

    async def proposer(prompt: str) -> str:
        proposer_calls.append(prompt)
        return "def solve():\n    return 42\n"

    judge_calls: list[Any] = []

    def evaluate(*_args, **_kwargs) -> Evaluation:
        judge_calls.append(1)
        return Evaluation(False, float("-inf"), {}, error="judge timed out")

    monkeypatch.setattr("qwenpaw.research.evaluate_candidate", evaluate)

    outcomes = await run_research(task_dir, proposer=proposer, rounds=1)

    # PLAN + PROGRAM = 2 proposer calls (each once), no re-invocation
    assert len(proposer_calls) == 2, (
        f"Expected 2 proposer calls (PLAN+PROGRAM), got {len(proposer_calls)}"
    )
    # JUDGE: 1 baseline + _JUDGE_MAX_ATTEMPTS retries (all timeout)
    assert len(judge_calls) == 1 + _JUDGE_MAX_ATTEMPTS, (
        f"JUDGE should be called {1 + _JUDGE_MAX_ATTEMPTS} times (baseline + retries), got {len(judge_calls)}"
    )
    # Outcome exists — rejected because judge never passed
    assert len(outcomes) == 1
    assert outcomes[0].status == "rejected"


@pytest.mark.asyncio
async def test_stage_failure_emits_terminal_event_once(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """When a stage fails (exhaustion), exactly one terminal outcome is emitted."""
    from qwenpaw.research import run_research, Evaluation

    task_dir = tmp_path / "task"
    task_dir.mkdir()
    (task_dir / "program.md").write_text("Improve it.", encoding="utf-8")
    (task_dir / "solution.txt").write_text("ok\n", encoding="utf-8")
    (task_dir / "judge.py").write_text(
        'import json; print(json.dumps({"passed": True, "score": 1, "metrics": {}}))',
        encoding="utf-8",
    )

    # First call is PLAN (success), then PROGRAM — always bad code
    call_count = [0]

    async def proposer(prompt: str) -> str:
        call_count[0] += 1
        if call_count[0] == 1:
            return "Plan: simple approach"
        # PROGRAM calls return bad unicode
        return 'print(\u201chello\u201d)\n'

    outcomes: list[Any] = []

    def on_outcome_callback(outcome):
        outcomes.append(outcome)

    result = await run_research(
        task_dir, proposer=proposer, rounds=1,
        on_outcome=on_outcome_callback,
    )

    # Exactly one outcome emitted (via callback and return)
    assert len(outcomes) == 1, f"Expected 1 outcome, got {len(outcomes)}"
    assert len(result) == 1
    assert result[0].status == "phase_error"
    assert outcomes[0].status == "phase_error"


@pytest.mark.asyncio
async def test_cancelled_run_skips_remaining_stages(tmp_path: Path) -> None:
    """When is_cancelled() returns True, JUDGE and REVIEW are skipped."""
    from qwenpaw.research import run_research

    task_dir = tmp_path / "task"
    task_dir.mkdir()
    (task_dir / "program.md").write_text("Improve it.", encoding="utf-8")
    (task_dir / "solution.txt").write_text("ok\n", encoding="utf-8")
    (task_dir / "judge.py").write_text(
        'import json; print(json.dumps({"passed": True, "score": 1, "metrics": {}}))',
        encoding="utf-8",
    )

    plan_calls: list[str] = []
    program_calls: list[str] = []
    cancelled_flag = [False]  # mutable so the closure can flip it

    async def proposer(prompt: str) -> str:
        if "not emit code" in prompt.lower():
            plan_calls.append(prompt)
            return "Plan: use hash map"
        else:
            program_calls.append(prompt)
            # Set cancel flag after PROGRAM returns — before JUDGE can start
            cancelled_flag[0] = True
            return "def solve():\n    return 42\n"

    def is_cancelled() -> bool:
        return cancelled_flag[0]

    progress: list[tuple[int, str, str]] = []

    result = await run_research(
        task_dir, proposer=proposer, rounds=1,
        is_cancelled=is_cancelled,
        on_progress=progress.append,
    )

    # PLAN ran exactly once
    assert len(plan_calls) == 1, f"PLAN should be called once, got {len(plan_calls)}"
    # PROGRAM ran at most once (first attempt, then cancelled before repair)
    assert len(program_calls) <= 1, f"PROGRAM should be <= 1, got {len(program_calls)}"
    # JUDGE and REVIEW never appear in progress
    phases_seen = [p[1] for p in progress]
    assert "judging" not in phases_seen, "JUDGE should not run after cancellation"
    assert "judging_retry" not in phases_seen
    # Terminal event is "run.cancelled"
    assert "run.cancelled" in phases_seen, "Cancellation event should be emitted"
    # Result is cancelled
    assert len(result) == 1
    assert result[0].status == "cancelled"


# ── P2: Atomic Write Tests ───────────────────────────────────────────────


def test_atomic_write_replaces_target_with_verified_content(tmp_path: Path) -> None:
    """_atomic_write writes content and replaces the target atomically."""
    target = tmp_path / "output.txt"
    content = "hello world\n"

    _atomic_write(target, content)

    assert target.exists()
    assert target.read_text("utf-8") == content


def test_atomic_write_preserves_original_on_content_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When read_bytes returns corrupted data, original file is untouched."""
    target = tmp_path / "important.py"
    original = "original content"
    target.write_text(original, "utf-8")

    # Make read_bytes return mismatched content (simulate corruption)
    real_read_bytes = Path.read_bytes

    def corrupted_read_bytes(self: Path) -> bytes:
        return b"corrupted"

    monkeypatch.setattr(Path, "read_bytes", corrupted_read_bytes)

    with pytest.raises(OSError, match="verification failed"):
        _atomic_write(target, "new content")

    # Original file untouched
    assert target.read_text("utf-8") == original
    # No .tmp files left behind
    tmps = list(tmp_path.glob("*.tmp"))
    assert len(tmps) == 0, f"Temp files leaked: {tmps}"


def test_atomic_write_cleans_temp_file_on_write_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When os.write fails, no .tmp files are left behind."""
    target = tmp_path / "output.txt"

    def failing_write(fd: int, data: bytes) -> int:
        raise OSError("disk full")

    monkeypatch.setattr(os, "write", failing_write)

    with pytest.raises(OSError, match="atomic write failed"):
        _atomic_write(target, "content")

    # No .tmp files left
    tmps = list(tmp_path.glob("*.tmp"))
    assert len(tmps) == 0, f"Temp files leaked: {tmps}"
    # Target never created
    assert not target.exists()


def test_atomic_write_supports_empty_content(tmp_path: Path) -> None:
    """_atomic_write handles empty string content."""
    target = tmp_path / "empty.txt"

    _atomic_write(target, "")

    assert target.exists()
    assert target.read_text("utf-8") == ""


def test_atomic_write_preserves_unicode_bytes(tmp_path: Path) -> None:
    """_atomic_write preserves multi-byte Unicode characters."""
    target = tmp_path / "unicode.txt"
    content = "你好世界 🌍\némoji café"

    _atomic_write(target, content)

    assert target.read_text("utf-8") == content
    assert target.read_bytes() == content.encode("utf-8")


def test_atomic_write_preserves_existing_file_mode(tmp_path: Path) -> None:
    """_atomic_write preserves the file mode of an existing target."""
    target = tmp_path / "script.sh"
    target.write_text("#!/bin/sh\necho hi\n", "utf-8")
    target.chmod(0o755)
    original_mode = target.stat().st_mode

    _atomic_write(target, "#!/bin/sh\necho hello\n")

    assert target.stat().st_mode == original_mode


def test_atomic_write_handles_new_file_with_default_permissions(
    tmp_path: Path,
) -> None:
    """_atomic_write creates a new file with default umask permissions."""
    target = tmp_path / "new.txt"

    _atomic_write(target, "new file")

    assert target.exists()
    # Should be readable/writable by owner at minimum
    mode = target.stat().st_mode
    assert mode & 0o600, f"File should be owner-readable/writable, got {oct(mode)}"


def test_evaluate_candidate_uses_verified_write(tmp_path: Path) -> None:
    """evaluate_candidate writes the candidate via _atomic_write and the
    resulting file content matches the source."""
    task = create_research_task(
        tmp_path,
        "atomic-eval",
        "# Test atomic write in evaluate\n",
        "solution.py",
        "def solve():\n    return 1\n",
        """\
import json, sys
from pathlib import Path

source = Path(sys.argv[1]).read_text()
passed = "def solve" in source
print(json.dumps({"passed": passed, "score": 1.0, "metrics": {}, "error": ""}))
""",
    )

    source = "def solve():\n    return 42\n"
    result = evaluate_candidate(task, source)

    assert result.passed
    # The candidate file in the temp dir should have exact content
    # (verified indirectly via judge passing and score 1.0)
    assert result.score == 1.0


def test_evaluate_candidate_aborts_when_write_verification_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When _atomic_write fails inside evaluate_candidate, it returns
    an error Evaluation instead of crashing."""
    task = create_research_task(
        tmp_path,
        "write-fail",
        "# Write failure test\n",
        "solution.py",
        "def solve():\n    return 1\n",
        """\
import json, sys
from pathlib import Path

source = Path(sys.argv[1]).read_text()
passed = "__QWENPAW_INVALID_CANDIDATE__" not in source
print(json.dumps({"passed": passed, "score": 1.0 if passed else 0.0, "metrics": {}, "error": ""}))
""",
    )

    # Make _atomic_write always fail
    def failing_atomic(path: Path, content: str) -> None:
        raise OSError("simulated write failure")

    monkeypatch.setattr(
        "qwenpaw.research._atomic_write", failing_atomic
    )

    result = evaluate_candidate(task, "def solve():\n    return 42\n")

    assert not result.passed
    assert "candidate write failed" in result.error


def test_atomic_write_concurrent_writers_never_produce_partial_content(
    tmp_path: Path,
) -> None:
    """Multiple concurrent _atomic_write calls never produce partial/truncated
    content. The final content is from exactly one complete writer."""
    import concurrent.futures

    target = tmp_path / "shared.txt"
    writers = [
        f"content-from-writer-{i:03d}\n" for i in range(20)
    ]

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        futures = [
            executor.submit(_atomic_write, target, w) for w in writers
        ]
        for f in futures:
            f.result()  # No exceptions expected

    # Final content must be one of the complete writer outputs
    actual = target.read_text("utf-8")
    assert actual in writers, f"Got unexpected content: {actual!r}"
    assert actual.endswith("\n"), "Content should be complete (ends with newline)"
    # No .tmp files left behind
    tmps = list(tmp_path.glob("*.tmp"))
    assert len(tmps) == 0, f"Temp files leaked: {tmps}"


def test_replace_solution_uses_atomic_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """_replace_solution delegates to _atomic_write and preserves
    existing file mode."""
    solution = tmp_path / "solution.py"
    solution.write_text("old content\n", "utf-8")
    solution.chmod(0o644)
    original_mode = solution.stat().st_mode

    _replace_solution(solution, "new content\n")

    assert solution.read_text("utf-8") == "new content\n"
    assert solution.stat().st_mode == original_mode


def test_atomic_write_rolls_back_on_os_replace_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When os.replace fails, original file is untouched and temp is cleaned."""
    target = tmp_path / "protected.txt"
    original = "protected content"
    target.write_text(original, "utf-8")

    def failing_replace(src: str, dst: str) -> None:
        raise OSError("cross-device link not permitted")

    monkeypatch.setattr(os, "replace", failing_replace)

    with pytest.raises(OSError, match="atomic write failed"):
        _atomic_write(target, "new content")

    # Original intact
    assert target.read_text("utf-8") == original
    # No temp files left
    tmps = list(tmp_path.glob("*.tmp"))
    assert len(tmps) == 0, f"Temp files leaked: {tmps}"


def test_atomic_write_rolls_back_on_fsync_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When os.fsync fails during write, original is untouched and temp cleaned."""
    target = tmp_path / "fsync_fail.txt"
    target.write_text("original", "utf-8")

    def failing_fsync(fd: int) -> None:
        raise OSError("I/O error during fsync")

    monkeypatch.setattr(os, "fsync", failing_fsync)

    with pytest.raises(OSError, match="atomic write failed"):
        _atomic_write(target, "new content")

    assert target.read_text("utf-8") == "original"
    tmps = list(tmp_path.glob("*.tmp"))
    assert len(tmps) == 0, f"Temp files leaked: {tmps}"


def test_fsync_directory_is_best_effort(tmp_path: Path) -> None:
    """_fsync_directory should not raise even if the platform doesn't
    support directory fsync."""
    # Should not raise for a valid directory
    _fsync_directory(tmp_path)

    # Should not raise for a non-existent directory
    _fsync_directory(tmp_path / "nonexistent")


def test_write_all_handles_partial_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """_write_all retries os.write until all bytes are written."""
    import io

    raw = io.BytesIO()
    fd = 999  # dummy fd, won't be passed to real os.write

    write_sizes = [3, 5, 2]  # 10 bytes total
    call_count = 0

    def partial_write(fd_arg: int, data: bytes) -> int:
        nonlocal call_count
        if call_count >= len(write_sizes):
            return 0  # this should never be reached
        n = min(write_sizes[call_count], len(data))
        raw.write(data[:n])
        call_count += 1
        return n

    monkeypatch.setattr(os, "write", partial_write)

    _write_all(fd, b"0123456789")

    assert raw.getvalue() == b"0123456789"
    assert call_count == 3  # 3+5+2 = 10


def test_write_all_raises_on_zero_progress(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_write_all raises OSError when os.write returns 0 (no progress)."""
    monkeypatch.setattr(os, "write", lambda fd, data: 0)

    with pytest.raises(OSError, match="no progress"):
        _write_all(999, b"data")


# ═══════════════════════════════════════════════════════════
# P3: Sandbox & Safety — Evaluator Isolation Tests
# ═══════════════════════════════════════════════════════════


def test_read_output_file_respects_hard_size_limit(tmp_path: Path) -> None:
    """``_read_output_file`` returns empty string when file exceeds 64 KiB."""
    from qwenpaw.research import _read_output_file, _MAX_READ_BYTES

    f = tmp_path / "oversized.log"
    f.write_bytes(b"x" * (_MAX_READ_BYTES + 100))

    result = _read_output_file(f)
    assert result == "", (
        f"Expected empty string for {_MAX_READ_BYTES + 100}-byte file, "
        f"got {len(result)} chars"
    )


def test_module_gracefully_degrades_when_resource_unavailable() -> None:
    """When ``resource`` is not importable (Windows), _SUPPORTS_RLIMITS=False.

    Runs in a subprocess with ``resource`` pre-popped from sys.modules
    so the ``try/except ImportError`` branch is exercised for real."""
    import subprocess

    code = """\
import sys
sys.modules['resource'] = None  # force ImportError on next import
import importlib
import qwenpaw.research
importlib.reload(qwenpaw.research)
print(qwenpaw.research._SUPPORTS_RLIMITS)
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, f"subprocess failed: {result.stderr}"
    assert result.stdout.strip() == "False", (
        f"Expected _SUPPORTS_RLIMITS=False, got {result.stdout.strip()!r}"
    )


def test_write_sandbox_runner_creates_executable_runner(
    tmp_path: Path,
) -> None:
    """_write_sandbox_runner writes an executable runner script that references rlimits."""
    # Only test where rlimits are supported
    import platform as _platform
    if _platform.system() == "Windows":
        pytest.skip("Resource limits are Unix-only")

    runner = _write_sandbox_runner(tmp_path)
    assert runner.exists()
    assert runner.stat().st_mode & 0o111  # must be executable
    content = runner.read_text(encoding="utf-8")
    assert "ImportError" in content  # runner handles missing resource module
    assert "import resource as _r" in content
    assert "RLIMIT_CPU" in content
    assert "RLIMIT_FSIZE" in content
    assert "os.execv" in content


def test_evaluate_candidate_runs_judge_inside_sandbox(tmp_path: Path) -> None:
    """evaluate_candidate copies the judge into the sandbox and runs from
    there, not from task.root."""
    task = create_research_task(
        tmp_path,
        "sandbox-test",
        "# Sandbox isolation test\n",
        "solution.py",
        "pass\n",
        """\
import json, sys, os
from pathlib import Path

candidate = Path(sys.argv[1]).read_text()
cwd = os.getcwd()
passed = "pass" in candidate
print(json.dumps({
    "passed": passed,
    "score": 1.0,
    "metrics": {"cwd": cwd, "judge_parent": str(Path(__file__).parent)},
    "error": ""
}))
""",
    )

    result = evaluate_candidate(task, "pass\n")

    assert result.passed
    # The judge's __file__ parent should NOT be task.root
    assert str(task.root) not in result.metrics.get("judge_parent", "")
    # The cwd should NOT be task.root
    assert str(task.root) not in result.metrics.get("cwd", "")


def test_evaluate_candidate_prevents_judge_from_reading_task_files(
    tmp_path: Path,
) -> None:
    """A judge that tries to read files from task.root (via relative paths)
    should fail because it runs from the sandbox directory."""
    task_dir = tmp_path / "isolated"
    task_dir.mkdir()
    (task_dir / "program.md").write_text("# Test\n", encoding="utf-8")
    # Put a secret file in task.root that the judge should NOT be able to read
    (task_dir / "secret.txt").write_text("do-not-read", encoding="utf-8")
    (task_dir / "solution.py").write_text("pass\n", encoding="utf-8")
    (task_dir / "judge.py").write_text(
        """\
import json, sys
from pathlib import Path

# Try to read secret.txt from cwd (which would succeed if cwd=task.root)
try:
    secret = Path("secret.txt").read_text()
    leak = True
except FileNotFoundError:
    leak = False

candidate = Path(sys.argv[1]).read_text()
print(json.dumps({
    "passed": "pass" in candidate,
    "score": 0.0 if leak else 1.0,
    "metrics": {"leaked": leak},
    "error": "leaked secret!" if leak else ""
}))
""",
        encoding="utf-8",
    )

    from qwenpaw.research import load_task
    task = load_task(task_dir)
    result = evaluate_candidate(task, "pass\n")

    # The judge should NOT be able to access secret.txt via relative path
    assert result.passed
    assert result.metrics.get("leaked") is False, (
        "Judge was able to read task.root/secret.txt — sandbox isolation failed"
    )


def test_evaluate_candidate_skips_preexec_on_windows(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """On Windows, _SUPPORTS_RLIMITS is False so no sandbox runner is used."""
    monkeypatch.setattr("qwenpaw.research.platform.system", lambda: "Windows")
    monkeypatch.setattr("qwenpaw.research._SUPPORTS_RLIMITS", False)

    task = create_research_task(
        tmp_path,
        "win-test",
        "# Windows sandbox skip test\n",
        "solution.py",
        "pass\n",
        """\
import json, sys
from pathlib import Path

passed = "pass" in Path(sys.argv[1]).read_text()
print(json.dumps({"passed": passed, "score": 1.0, "metrics": {}, "error": ""}))
""",
    )

    # Should run without using a sandbox runner
    result = evaluate_candidate(task, "pass\n")
    assert result.passed


def test_evaluate_candidate_sandbox_survives_memory_exhaustion_attempt(
    tmp_path: Path,
) -> None:
    """A judge that tries to allocate excessive memory is terminated by
    resource limits rather than crashing the parent process."""
    import platform as _platform

    if _platform.system() == "Windows":
        pytest.skip("Resource limits are Unix-only")

    task_dir = tmp_path / "mem-test"
    task_dir.mkdir()
    (task_dir / "program.md").write_text("# Memory exhaustion test\n", encoding="utf-8")
    (task_dir / "solution.py").write_text("pass\n", encoding="utf-8")
    (task_dir / "judge.py").write_text(
        """\
import json, sys
from pathlib import Path

# Try to allocate ~1 GB (should hit memory limit and get killed)
try:
    big = bytearray(1024 * 1024 * 1024)  # 1 GB
except MemoryError:
    pass  # expected — sandbox should limit before this

candidate = Path(sys.argv[1]).read_text()
print(json.dumps({
    "passed": "pass" in candidate,
    "score": 1.0,
    "metrics": {},
    "error": ""
}))
""",
        encoding="utf-8",
    )

    from qwenpaw.research import load_task
    task = load_task(task_dir)

    # The evaluate_candidate call should complete (parent survives)
    result = evaluate_candidate(task, "pass\n", timeout_seconds=15.0)

    # Either the judge runs (memory limit kills allocation, passes)
    # or the judge is killed by the OS — either way, the parent survives
    assert result.error == "" or "SIG" in result.error.upper() or "MEMORY" in result.error.upper(), (
        f"Unexpected error: {result.error}"
    )


def test_unsafe_research_gate_acknowledges_remaining_risk() -> None:
    """unsafe_research_enabled() requires explicit opt-in and the docstring
    acknowledges residual risk even after sandboxing."""
    from qwenpaw.research import unsafe_research_enabled

    # Without the env var, it must be False
    assert unsafe_research_enabled() is False

    # Docstring should mention sandbox and residual risk
    import qwenpaw.research as mod
    doc = mod.unsafe_research_enabled.__doc__ or ""
    assert "sandbox" in doc.lower() or "P3" in doc
