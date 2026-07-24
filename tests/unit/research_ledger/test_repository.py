"""Unit tests for BaseResearchLedgerRepository and PostgresResearchLedgerRepository.

Uses SQLite (aiosqlite) in-memory for fast isolated tests of the repository logic.
The SQLAlchemy abstraction ensures behavioral equivalence with PostgreSQL.
"""

import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from qwenpaw.research_ledger.repository import (
    BaseResearchLedgerRepository,
    PostgresResearchLedgerRepository,
)
from qwenpaw.research_ledger.schema import Base


@pytest.fixture
async def repo():
    """Create a repository backed by async SQLite in-memory."""
    repository = PostgresResearchLedgerRepository(
        "sqlite+aiosqlite://", pool_size=0, max_overflow=0
    )
    await repository.initialize()
    yield repository
    await repository.close()


class TestRepositoryABC:
    """Verify ABC contract is complete and concrete impl satisfies it."""

    def test_base_is_abstract(self):
        with pytest.raises(TypeError):
            BaseResearchLedgerRepository()  # type: ignore[abstract]

    def test_postgres_impl_is_concrete(self):
        assert issubclass(PostgresResearchLedgerRepository, BaseResearchLedgerRepository)


class TestTaskOperations:
    @pytest.mark.asyncio
    async def test_create_and_get_task(self, repo):
        await repo.create_task(
            task_id="task-1",
            program="Solve FizzBuzz",
            solution_name="fizzbuzz.py",
            solution_source="def fizzbuzz(): pass",
            judge_source="def judge(): return True",
        )
        task = await repo.get_task("task-1")
        assert task is not None
        assert task.task_id == "task-1"
        assert task.program == "Solve FizzBuzz"
        assert task.solution_name == "fizzbuzz.py"

    @pytest.mark.asyncio
    async def test_get_nonexistent_task(self, repo):
        task = await repo.get_task("nonexistent")
        assert task is None

    @pytest.mark.asyncio
    async def test_list_tasks(self, repo):
        for i in range(3):
            await repo.create_task(
                task_id=f"task-{i}",
                program=f"Program {i}",
                solution_name="sol.py",
                solution_source="x",
                judge_source="y",
            )
        tasks = await repo.list_tasks(limit=2)
        assert len(tasks) == 2
        # Verify all tasks exist (order may vary with same-second inserts)
        task_ids = {t.task_id for t in tasks}
        assert task_ids.issubset({"task-0", "task-1", "task-2"})


class TestRunOperations:
    @pytest.mark.asyncio
    async def test_create_run_requires_task(self, repo):
        # Foreign key constraint
        with pytest.raises(Exception):
            await repo.create_run(
                run_id="run-1",
                task_id="nonexistent-task",
                agent_id="agent-1",
                rounds=5,
            )

    @pytest.mark.asyncio
    async def test_create_and_get_run(self, repo):
        await repo.create_task("task-1", "p", "s.py", "x", "y")
        await repo.create_run(
            run_id="run-1", task_id="task-1", agent_id="agent-a", rounds=5
        )
        run = await repo.get_run("run-1")
        assert run is not None
        assert run.run_id == "run-1"
        assert run.task_id == "task-1"
        assert run.agent_id == "agent-a"
        assert run.rounds == 5
        assert run.status == "running"
        assert run.phase == "running"

    @pytest.mark.asyncio
    async def test_list_runs(self, repo):
        await repo.create_task("task-1", "p", "s.py", "x", "y")
        await repo.create_run("run-1", "task-1", "a", 3)
        await repo.create_run("run-2", "task-1", "b", 5)
        runs = await repo.list_runs("task-1")
        assert len(runs) == 2

    @pytest.mark.asyncio
    async def test_update_run_status(self, repo):
        await repo.create_task("task-1", "p", "s.py", "x", "y")
        await repo.create_run("run-1", "task-1", "a", 5)
        await repo.update_run_status("run-1", "completed", "done", "")
        run = await repo.get_run("run-1")
        assert run.status == "completed"
        assert run.phase == "done"

    @pytest.mark.asyncio
    async def test_update_run_progress(self, repo):
        await repo.create_task("task-1", "p", "s.py", "x", "y")
        await repo.create_run("run-1", "task-1", "a", 5)
        await repo.update_run_progress("run-1", completed_rounds=3, current_round=3)
        run = await repo.get_run("run-1")
        assert run.completed_rounds == 3
        assert run.current_round == 3

    @pytest.mark.asyncio
    async def test_finish_run(self, repo):
        await repo.create_task("task-1", "p", "s.py", "x", "y")
        await repo.create_run("run-1", "task-1", "a", 5)
        await repo.finish_run("run-1", "succeeded", "succeeded", "")
        run = await repo.get_run("run-1")
        assert run.status == "succeeded"
        assert run.phase == "succeeded"
        assert run.finished_at is not None


class TestOutcomeOperations:
    @pytest.mark.asyncio
    async def test_record_and_list_outcomes(self, repo):
        await repo.create_task("task-1", "p", "s.py", "x", "y")
        await repo.create_run("run-1", "task-1", "a", 5)

        await repo.record_outcome(
            run_id="run-1", round=1, status="kept", passed=True,
            baseline_score=0.5, candidate_score=0.8, improvement=0.3,
            metrics='{"accuracy": 0.9}', error="",
            parent_source="def f(): pass", candidate_source="def f(): return 1",
        )
        outcomes = await repo.list_outcomes("run-1")
        assert len(outcomes) == 1
        assert outcomes[0].round == 1
        assert outcomes[0].status == "kept"
        assert outcomes[0].passed is True
        assert outcomes[0].baseline_score == 0.5
        assert outcomes[0].candidate_score == 0.8
        assert outcomes[0].improvement == 0.3

    @pytest.mark.asyncio
    async def test_unique_run_round_violation(self, repo):
        await repo.create_task("task-1", "p", "s.py", "x", "y")
        await repo.create_run("run-1", "task-1", "a", 5)

        await repo.record_outcome(
            "run-1", 1, "kept", True, 0.5, 0.8, 0.3, "{}", "", "x", "y",
        )
        with pytest.raises(Exception):
            await repo.record_outcome(
                "run-1", 1, "kept", True, 0.5, 0.8, 0.3, "{}", "", "x", "y",
            )


class TestEventOperations:
    @pytest.mark.asyncio
    async def test_record_and_list_events(self, repo):
        await repo.create_task("task-1", "p", "s.py", "x", "y")
        await repo.create_run("run-1", "task-1", "a", 5)

        await repo.record_event("run-1", phase="started", round=None, detail='{"msg":"begin"}')
        await repo.record_event("run-1", phase="round_complete", round=1)
        events = await repo.list_events("run-1")
        assert len(events) == 2
        assert events[0].phase == "started"
        assert events[1].round == 1


class TestDialogRunOperations:
    @pytest.mark.asyncio
    async def test_create_and_get_dialog(self, repo):
        await repo.create_task("task-1", "p", "s.py", "x", "y")
        await repo.create_dialog_run(
            plan_id="plan-1", task_id="task-1", goal="Optimize solution", rounds=10
        )
        dialog = await repo.get_dialog_run("plan-1")
        assert dialog is not None
        assert dialog.plan_id == "plan-1"
        assert dialog.status == "planning"
        assert dialog.goal == "Optimize solution"

    @pytest.mark.asyncio
    async def test_update_and_finish_dialog(self, repo):
        await repo.create_task("task-1", "p", "s.py", "x", "y")
        await repo.create_run("run-1", "task-1", "a", 5)
        await repo.create_dialog_run("plan-1", "task-1", "Goal", 10)

        await repo.update_dialog_run("plan-1", status="planned", run_id="run-1")
        dialog = await repo.get_dialog_run("plan-1")
        assert dialog.status == "planned"
        assert dialog.run_id == "run-1"

        await repo.finish_dialog_run("plan-1", "planned")
        dialog = await repo.get_dialog_run("plan-1")
        assert dialog.finished_at is not None


class TestSubmissionOperations:
    @pytest.mark.asyncio
    async def test_record_and_list_submissions(self, repo):
        await repo.create_task("task-1", "p", "s.py", "x", "y")
        await repo.create_run("run-1", "task-1", "a", 5)
        outcome = await repo.record_outcome(
            "run-1", 1, "kept", True, 0.5, 0.8, 0.3, "{}", "", "x", "y"
        )

        await repo.record_submission(
            outcome_id=outcome.id,
            url="https://leetcode.com/submissions/detail/123",
            language="python",
            status="accepted",
            verdict="Accepted",
            error="",
        )
        subs = await repo.list_submissions(outcome.id)
        assert len(subs) == 1
        assert subs[0].status == "accepted"
        assert subs[0].language == "python"


class TestArtifactOperations:
    @pytest.mark.asyncio
    async def test_record_and_list_artifacts(self, repo):
        await repo.create_task("task-1", "p", "s.py", "x", "y")
        await repo.create_run("run-1", "task-1", "a", 5)

        await repo.record_artifact(
            run_id="run-1",
            local_path="/artifacts/run-1/source.py",
            content_hash="abc123",
            size_bytes=256,
            content_type="text/x-python",
        )
        artifacts = await repo.list_artifacts("run-1")
        assert len(artifacts) == 1
        assert artifacts[0].content_hash == "abc123"
        assert artifacts[0].content_type == "text/x-python"

    @pytest.mark.asyncio
    async def test_unique_run_path_violation(self, repo):
        await repo.create_task("task-1", "p", "s.py", "x", "y")
        await repo.create_run("run-1", "task-1", "a", 5)

        await repo.record_artifact("run-1", "/tmp/a.py", "h1", 100, "text/plain")
        with pytest.raises(Exception):
            await repo.record_artifact("run-1", "/tmp/a.py", "h2", 200, "text/plain")


class TestLifecycle:
    @pytest.mark.asyncio
    async def test_initialize_creates_tables(self):
        repo = PostgresResearchLedgerRepository("sqlite+aiosqlite://")
        try:
            await repo.initialize()
            # Verify we can insert and query
            await repo.create_task("t1", "p", "s.py", "x", "y")
            task = await repo.get_task("t1")
            assert task is not None
        finally:
            await repo.close()

    @pytest.mark.asyncio
    async def test_engine_not_initialized_raises(self):
        repo = PostgresResearchLedgerRepository("sqlite+aiosqlite://")
        with pytest.raises(RuntimeError, match="not initialized"):
            await repo.get_task("x")
        # No close needed — engine was never created
