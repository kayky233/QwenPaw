"""Integration tests for Alembic migration and PostgreSQL connectivity.

Requires DATABASE_URL environment variable pointing to a PostgreSQL instance.
Skip tests if DATABASE_URL is not set (for CI/laptop without PG).
"""

import os

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from qwenpaw.research_ledger.schema import Base


pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not os.environ.get("DATABASE_URL"),
        reason="DATABASE_URL not set — skipping PostgreSQL integration tests",
    ),
]

DATABASE_URL = os.environ.get("DATABASE_URL", "")


@pytest.fixture
async def engine():
    """Create an async engine against the test database, clean up after."""
    eng = create_async_engine(DATABASE_URL, echo=False)
    # Drop all tables first for a clean slate
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await eng.dispose()


class TestMigrationUpgradeDowngrade:
    """Verify that the initial migration creates all 7 tables correctly."""

    @pytest.mark.asyncio
    async def test_all_tables_exist(self, engine):
        async with engine.connect() as conn:
            result = await conn.execute(
                text(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = 'public' "
                    "AND table_name LIKE 'research_%' "
                    "ORDER BY table_name"
                )
            )
            tables = [row[0] for row in result]
            assert tables == [
                "research_artifacts",
                "research_dialog_runs",
                "research_events",
                "research_outcomes",
                "research_runs",
                "research_submissions",
                "research_tasks",
            ]

    @pytest.mark.asyncio
    async def test_research_tasks_columns(self, engine):
        async with engine.connect() as conn:
            result = await conn.execute(
                text(
                    "SELECT column_name, data_type, is_nullable "
                    "FROM information_schema.columns "
                    "WHERE table_name = 'research_tasks' "
                    "ORDER BY ordinal_position"
                )
            )
            cols = {row[0]: (row[1], row[2]) for row in result}
            assert "task_id" in cols
            assert "program" in cols
            assert cols["program"][1] == "NO"  # NOT NULL


class TestForeignKeyConstraints:
    @pytest.mark.asyncio
    async def test_runs_task_fk(self, engine):
        """Insert run referencing nonexistent task should fail."""
        async with engine.begin() as conn:
            with pytest.raises(Exception):
                await conn.execute(
                    text(
                        "INSERT INTO research_runs (run_id, task_id, agent_id, rounds) "
                        "VALUES ('run-1', 'nonexistent', 'agent-1', 5)"
                    )
                )

    @pytest.mark.asyncio
    async def test_outcomes_run_fk(self, engine):
        async with engine.begin() as conn:
            with pytest.raises(Exception):
                await conn.execute(
                    text(
                        "INSERT INTO research_outcomes (run_id, round, status, passed, "
                        "baseline_score, improvement, metrics, error, parent_source, candidate_source) "
                        "VALUES ('nonexistent', 1, 'kept', true, 0.5, 0.3, '{}', '', '', '')"
                    )
                )


class TestUniqueConstraints:
    @pytest.mark.asyncio
    async def test_outcome_unique_run_round(self, engine):
        async with engine.begin() as conn:
            # Insert a task and run first
            await conn.execute(
                text(
                    "INSERT INTO research_tasks (task_id, program, solution_name, "
                    "solution_source, judge_source) "
                    "VALUES ('task-1', 'p', 's.py', 'x', 'y')"
                )
            )
            await conn.execute(
                text(
                    "INSERT INTO research_runs (run_id, task_id, agent_id, rounds) "
                    "VALUES ('run-1', 'task-1', 'agent-a', 5)"
                )
            )
            await conn.execute(
                text(
                    "INSERT INTO research_outcomes (run_id, round, status, passed, "
                    "baseline_score, improvement, metrics, error, parent_source, candidate_source) "
                    "VALUES ('run-1', 1, 'kept', true, 0.5, 0.3, '{}', '', '', '')"
                )
            )
            # Duplicate (run_id, round) should fail
            with pytest.raises(Exception):
                await conn.execute(
                    text(
                        "INSERT INTO research_outcomes (run_id, round, status, passed, "
                        "baseline_score, improvement, metrics, error, parent_source, candidate_source) "
                        "VALUES ('run-1', 1, 'rejected', false, 0.5, 0.0, '{}', '', '', '')"
                    )
                )

    @pytest.mark.asyncio
    async def test_artifact_unique_run_path(self, engine):
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO research_tasks (task_id, program, solution_name, "
                    "solution_source, judge_source) "
                    "VALUES ('task-1', 'p', 's.py', 'x', 'y')"
                )
            )
            await conn.execute(
                text(
                    "INSERT INTO research_runs (run_id, task_id, agent_id, rounds) "
                    "VALUES ('run-1', 'task-1', 'agent-a', 5)"
                )
            )
            await conn.execute(
                text(
                    "INSERT INTO research_artifacts (run_id, local_path, content_hash, "
                    "size_bytes, content_type) "
                    "VALUES ('run-1', '/tmp/a.py', 'abc', 100, 'text/plain')"
                )
            )
            with pytest.raises(Exception):
                await conn.execute(
                    text(
                        "INSERT INTO research_artifacts (run_id, local_path, content_hash, "
                        "size_bytes, content_type) "
                        "VALUES ('run-1', '/tmp/a.py', 'def', 200, 'text/plain')"
                    )
                )


class TestCRUDWithPostgreSQL:
    """End-to-end CRUD using raw SQL via the engine."""

    @pytest.mark.asyncio
    async def test_task_run_outcome_event_flow(self, engine):
        async with engine.begin() as conn:
            # Create task
            await conn.execute(
                text(
                    "INSERT INTO research_tasks (task_id, program, solution_name, "
                    "solution_source, judge_source) "
                    "VALUES ('task-1', 'FizzBuzz', 'fb.py', 'def f(): pass', 'def j(): pass')"
                )
            )

            # Create run
            await conn.execute(
                text(
                    "INSERT INTO research_runs (run_id, task_id, agent_id, rounds, started_at) "
                    "VALUES ('run-1', 'task-1', 'agent-a', 5, NOW())"
                )
            )

            # Record outcome
            await conn.execute(
                text(
                    "INSERT INTO research_outcomes (run_id, round, status, passed, "
                    "baseline_score, candidate_score, improvement, metrics, error, "
                    "parent_source, candidate_source) "
                    "VALUES ('run-1', 1, 'kept', true, 0.5, 0.9, 0.4, '{}', '', 'x', 'y')"
                )
            )

            # Record event
            await conn.execute(
                text(
                    "INSERT INTO research_events (run_id, phase, round, detail) "
                    "VALUES ('run-1', 'round_complete', 1, '{}')"
                )
            )

        # Verify reads
        async with engine.connect() as conn:
            # Check run
            result = await conn.execute(
                text("SELECT status, rounds FROM research_runs WHERE run_id = 'run-1'")
            )
            row = result.fetchone()
            assert row == ("running", 5)

            # Check outcome
            result = await conn.execute(
                text(
                    "SELECT status, passed, candidate_score "
                    "FROM research_outcomes WHERE run_id = 'run-1'"
                )
            )
            row = result.fetchone()
            assert row == ("kept", True, 0.9)

            # Check event
            result = await conn.execute(
                text("SELECT phase FROM research_events WHERE run_id = 'run-1'")
            )
            row = result.fetchone()
            assert row == ("round_complete",)

    @pytest.mark.asyncio
    async def test_cascade_delete_run_removes_outcomes(self, engine):
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO research_tasks (task_id, program, solution_name, "
                    "solution_source, judge_source) "
                    "VALUES ('t1', 'p', 's.py', 'x', 'y')"
                )
            )
            await conn.execute(
                text(
                    "INSERT INTO research_runs (run_id, task_id, agent_id, rounds) "
                    "VALUES ('run-x', 't1', 'a', 5)"
                )
            )
            await conn.execute(
                text(
                    "INSERT INTO research_outcomes (run_id, round, status, passed, "
                    "baseline_score, improvement, metrics, error, parent_source, candidate_source) "
                    "VALUES ('run-x', 1, 'kept', true, 0.5, 0.3, '{}', '', '', '')"
                )
            )

        # Delete the run — outcomes should cascade
        async with engine.begin() as conn:
            await conn.execute(
                text("DELETE FROM research_runs WHERE run_id = 'run-x'")
            )

        async with engine.connect() as conn:
            result = await conn.execute(
                text("SELECT COUNT(*) FROM research_outcomes WHERE run_id = 'run-x'")
            )
            count = result.scalar()
            assert count == 0
