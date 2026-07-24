"""Repository layer for the Research Ledger.

Follows the existing project pattern:
- ABC defines the contract (like BaseChatRepository / BaseJobRepository)
- PostgreSQL implementation using SQLAlchemy 2.0 Async Core

All public methods are async and use AsyncConnection for Core-style queries.
"""

from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Optional, Sequence

from sqlalchemy import (
    delete,
    insert,
    select,
    update,
)
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine

from qwenpaw.research_ledger.schema import (
    Base,
    ResearchArtifact,
    ResearchDialogRun,
    ResearchEvent,
    ResearchOutcome,
    ResearchRun,
    ResearchSubmission,
    ResearchTask,
)


# ── ABC ─────────────────────────────────────────────────────────────────────

class BaseResearchLedgerRepository(ABC):
    """Abstract contract for research ledger persistence.

    All methods are async. Concrete implementations handle the storage
    backend (PostgreSQL, in-memory for tests, etc.).
    """

    # ── Tasks ───────────────────────────────────────────────────────────

    @abstractmethod
    async def create_task(
        self,
        task_id: str,
        program: str,
        solution_name: str,
        solution_source: str,
        judge_source: str,
    ) -> ResearchTask: ...

    @abstractmethod
    async def get_task(self, task_id: str) -> Optional[ResearchTask]: ...

    @abstractmethod
    async def list_tasks(self, limit: int = 50) -> Sequence[ResearchTask]: ...

    # ── Runs ────────────────────────────────────────────────────────────

    @abstractmethod
    async def create_run(
        self,
        run_id: str,
        task_id: str,
        agent_id: str,
        rounds: int,
    ) -> ResearchRun: ...

    @abstractmethod
    async def get_run(self, run_id: str) -> Optional[ResearchRun]: ...

    @abstractmethod
    async def list_runs(self, task_id: str, limit: int = 50) -> Sequence[ResearchRun]: ...

    @abstractmethod
    async def update_run_status(
        self,
        run_id: str,
        status: str,
        phase: str,
        error: str = "",
    ) -> None: ...

    @abstractmethod
    async def update_run_progress(
        self,
        run_id: str,
        completed_rounds: int,
        current_round: Optional[int] = None,
    ) -> None: ...

    @abstractmethod
    async def finish_run(
        self,
        run_id: str,
        status: str,
        phase: str,
        error: str = "",
    ) -> None: ...

    # ── Outcomes ────────────────────────────────────────────────────────

    @abstractmethod
    async def record_outcome(
        self,
        run_id: str,
        round: int,
        status: str,
        passed: bool,
        baseline_score: float,
        candidate_score: Optional[float],
        improvement: float,
        metrics: str,
        error: str,
        parent_source: str,
        candidate_source: str,
    ) -> ResearchOutcome: ...

    @abstractmethod
    async def list_outcomes(self, run_id: str) -> Sequence[ResearchOutcome]: ...

    # ── Events ──────────────────────────────────────────────────────────

    @abstractmethod
    async def record_event(
        self,
        run_id: str,
        phase: str,
        round: Optional[int],
        detail: str = "{}",
    ) -> ResearchEvent: ...

    @abstractmethod
    async def list_events(self, run_id: str) -> Sequence[ResearchEvent]: ...

    # ── Dialog Runs ─────────────────────────────────────────────────────

    @abstractmethod
    async def create_dialog_run(
        self,
        plan_id: str,
        task_id: str,
        goal: str,
        rounds: int,
    ) -> ResearchDialogRun: ...

    @abstractmethod
    async def get_dialog_run(self, plan_id: str) -> Optional[ResearchDialogRun]: ...

    @abstractmethod
    async def update_dialog_run(
        self,
        plan_id: str,
        status: str,
        run_id: Optional[str] = None,
        error: str = "",
    ) -> None: ...

    @abstractmethod
    async def finish_dialog_run(
        self,
        plan_id: str,
        status: str,
        error: str = "",
    ) -> None: ...

    # ── Submissions ─────────────────────────────────────────────────────

    @abstractmethod
    async def record_submission(
        self,
        outcome_id: int,
        url: str,
        language: str,
        status: str,
        verdict: str,
        error: str,
    ) -> ResearchSubmission: ...

    @abstractmethod
    async def list_submissions(
        self, outcome_id: int
    ) -> Sequence[ResearchSubmission]: ...

    # ── Artifacts ───────────────────────────────────────────────────────

    @abstractmethod
    async def record_artifact(
        self,
        run_id: str,
        local_path: str,
        content_hash: str,
        size_bytes: int,
        content_type: str,
        outcome_id: Optional[int] = None,
    ) -> ResearchArtifact: ...

    @abstractmethod
    async def list_artifacts(self, run_id: str) -> Sequence[ResearchArtifact]: ...

    # ── Lifecycle ───────────────────────────────────────────────────────

    @abstractmethod
    async def initialize(self) -> None:
        """Create all tables if they don't exist."""
        ...

    @abstractmethod
    async def close(self) -> None:
        """Release resources (connection pool, etc.)."""
        ...


# ── PostgreSQL Implementation ───────────────────────────────────────────────

class PostgresResearchLedgerRepository(BaseResearchLedgerRepository):
    """PostgreSQL-backed repository using SQLAlchemy 2.0 Async Core.

    Uses asyncpg as the async driver. All queries use Core-style
    insert/select/update via AsyncConnection — no ORM Session.
    """

    def __init__(self, database_url: str, *, pool_size: int = 5, max_overflow: int = 10):
        self._database_url = database_url
        self._pool_size = pool_size
        self._max_overflow = max_overflow
        self._engine: Optional[AsyncEngine] = None

    def _get_engine(self) -> AsyncEngine:
        if self._engine is None:
            raise RuntimeError(
                "Engine not initialized. Call await repository.initialize() first."
            )
        return self._engine

    # ── Lifecycle ───────────────────────────────────────────────────────

    async def initialize(self) -> None:
        """Create the async engine and run DDL (CREATE TABLE IF NOT EXISTS)."""
        engine_kwargs: dict = {"echo": False}
        # pool_size/max_overflow are PostgreSQL-only; SQLite uses StaticPool
        if "sqlite" not in self._database_url:
            engine_kwargs["pool_size"] = self._pool_size
            engine_kwargs["max_overflow"] = self._max_overflow
            engine_kwargs["pool_pre_ping"] = True
        self._engine = create_async_engine(self._database_url, **engine_kwargs)
        async with self._engine.begin() as conn:
            # SQLite requires PRAGMA to enforce foreign keys
            if "sqlite" in self._database_url:
                from sqlalchemy import text
                await conn.execute(text("PRAGMA foreign_keys = ON"))
            await conn.run_sync(Base.metadata.create_all)

    async def close(self) -> None:
        """Dispose the connection pool."""
        if self._engine is not None:
            await self._engine.dispose()
            self._engine = None

    # ── Tasks ───────────────────────────────────────────────────────────

    async def create_task(
        self,
        task_id: str,
        program: str,
        solution_name: str,
        solution_source: str,
        judge_source: str,
    ) -> ResearchTask:
        engine = self._get_engine()
        async with engine.begin() as conn:
            await conn.execute(
                insert(ResearchTask).values(
                    task_id=task_id,
                    program=program,
                    solution_name=solution_name,
                    solution_source=solution_source,
                    judge_source=judge_source,
                )
            )
        return await self.get_task(task_id)  # type: ignore[return-value]

    async def get_task(self, task_id: str) -> Optional[ResearchTask]:
        engine = self._get_engine()
        async with engine.connect() as conn:
            result = await conn.execute(
                select(ResearchTask).where(ResearchTask.task_id == task_id)
            )
            row = result.first()
            return ResearchTask(**row._mapping) if row is not None else None

    async def list_tasks(self, limit: int = 50) -> Sequence[ResearchTask]:
        engine = self._get_engine()
        async with engine.connect() as conn:
            result = await conn.execute(
                select(ResearchTask)
                .order_by(ResearchTask.created_at.desc())
                .limit(limit)
            )
            return [ResearchTask(**r._mapping) for r in result.all()]

    # ── Runs ────────────────────────────────────────────────────────────

    async def create_run(
        self,
        run_id: str,
        task_id: str,
        agent_id: str,
        rounds: int,
    ) -> ResearchRun:
        engine = self._get_engine()
        now = datetime.now(timezone.utc)
        async with engine.begin() as conn:
            await conn.execute(
                insert(ResearchRun).values(
                    run_id=run_id,
                    task_id=task_id,
                    agent_id=agent_id,
                    rounds=rounds,
                    status="running",
                    phase="running",
                    started_at=now,
                )
            )
        return await self.get_run(run_id)  # type: ignore[return-value]

    async def get_run(self, run_id: str) -> Optional[ResearchRun]:
        engine = self._get_engine()
        async with engine.connect() as conn:
            result = await conn.execute(
                select(ResearchRun).where(ResearchRun.run_id == run_id)
            )
            row = result.first()
            return ResearchRun(**row._mapping) if row is not None else None

    async def list_runs(self, task_id: str, limit: int = 50) -> Sequence[ResearchRun]:
        engine = self._get_engine()
        async with engine.connect() as conn:
            result = await conn.execute(
                select(ResearchRun)
                .where(ResearchRun.task_id == task_id)
                .order_by(ResearchRun.created_at.desc())
                .limit(limit)
            )
            return [ResearchRun(**r._mapping) for r in result.all()]

    async def update_run_status(
        self,
        run_id: str,
        status: str,
        phase: str,
        error: str = "",
    ) -> None:
        engine = self._get_engine()
        async with engine.begin() as conn:
            await conn.execute(
                update(ResearchRun)
                .where(ResearchRun.run_id == run_id)
                .values(status=status, phase=phase, error=error)
            )

    async def update_run_progress(
        self,
        run_id: str,
        completed_rounds: int,
        current_round: Optional[int] = None,
    ) -> None:
        engine = self._get_engine()
        values = {"completed_rounds": completed_rounds}
        if current_round is not None:
            values["current_round"] = current_round
        async with engine.begin() as conn:
            await conn.execute(
                update(ResearchRun)
                .where(ResearchRun.run_id == run_id)
                .values(**values)
            )

    async def finish_run(
        self,
        run_id: str,
        status: str,
        phase: str,
        error: str = "",
    ) -> None:
        engine = self._get_engine()
        now = datetime.now(timezone.utc)
        async with engine.begin() as conn:
            await conn.execute(
                update(ResearchRun)
                .where(ResearchRun.run_id == run_id)
                .values(
                    status=status,
                    phase=phase,
                    error=error,
                    finished_at=now,
                )
            )

    # ── Outcomes ────────────────────────────────────────────────────────

    async def record_outcome(
        self,
        run_id: str,
        round: int,
        status: str,
        passed: bool,
        baseline_score: float,
        candidate_score: Optional[float],
        improvement: float,
        metrics: str,
        error: str,
        parent_source: str,
        candidate_source: str,
    ) -> ResearchOutcome:
        engine = self._get_engine()
        async with engine.begin() as conn:
            result = await conn.execute(
                insert(ResearchOutcome)
                .values(
                    run_id=run_id,
                    round=round,
                    status=status,
                    passed=passed,
                    baseline_score=baseline_score,
                    candidate_score=candidate_score,
                    improvement=improvement,
                    metrics=metrics,
                    error=error,
                    parent_source=parent_source,
                    candidate_source=candidate_source,
                )
                .returning(ResearchOutcome)
            )
            outcome_row = result.one()
            return ResearchOutcome(**outcome_row._mapping)

    async def list_outcomes(self, run_id: str) -> Sequence[ResearchOutcome]:
        engine = self._get_engine()
        async with engine.connect() as conn:
            result = await conn.execute(
                select(ResearchOutcome)
                .where(ResearchOutcome.run_id == run_id)
                .order_by(ResearchOutcome.round)
            )
            return [ResearchOutcome(**r._mapping) for r in result.all()]

    # ── Events ──────────────────────────────────────────────────────────

    async def record_event(
        self,
        run_id: str,
        phase: str,
        round: Optional[int],
        detail: str = "{}",
    ) -> ResearchEvent:
        engine = self._get_engine()
        async with engine.begin() as conn:
            result = await conn.execute(
                insert(ResearchEvent)
                .values(run_id=run_id, phase=phase, round=round, detail=detail)
                .returning(ResearchEvent)
            )
            event_row = result.one()
            return ResearchEvent(**event_row._mapping)

    async def list_events(self, run_id: str) -> Sequence[ResearchEvent]:
        engine = self._get_engine()
        async with engine.connect() as conn:
            result = await conn.execute(
                select(ResearchEvent)
                .where(ResearchEvent.run_id == run_id)
                .order_by(ResearchEvent.timestamp)
            )
            return [ResearchEvent(**r._mapping) for r in result.all()]

    # ── Dialog Runs ─────────────────────────────────────────────────────

    async def create_dialog_run(
        self,
        plan_id: str,
        task_id: str,
        goal: str,
        rounds: int,
    ) -> ResearchDialogRun:
        engine = self._get_engine()
        now = datetime.now(timezone.utc)
        async with engine.begin() as conn:
            await conn.execute(
                insert(ResearchDialogRun).values(
                    plan_id=plan_id,
                    task_id=task_id,
                    goal=goal,
                    rounds=rounds,
                    status="planning",
                    started_at=now,
                )
            )
        return await self.get_dialog_run(plan_id)  # type: ignore[return-value]

    async def get_dialog_run(self, plan_id: str) -> Optional[ResearchDialogRun]:
        engine = self._get_engine()
        async with engine.connect() as conn:
            result = await conn.execute(
                select(ResearchDialogRun).where(
                    ResearchDialogRun.plan_id == plan_id
                )
            )
            row = result.first()
            return ResearchDialogRun(**row._mapping) if row is not None else None

    async def update_dialog_run(
        self,
        plan_id: str,
        status: str,
        run_id: Optional[str] = None,
        error: str = "",
    ) -> None:
        engine = self._get_engine()
        values: dict = {"status": status, "error": error}
        if run_id is not None:
            values["run_id"] = run_id
        async with engine.begin() as conn:
            await conn.execute(
                update(ResearchDialogRun)
                .where(ResearchDialogRun.plan_id == plan_id)
                .values(**values)
            )

    async def finish_dialog_run(
        self,
        plan_id: str,
        status: str,
        error: str = "",
    ) -> None:
        engine = self._get_engine()
        now = datetime.now(timezone.utc)
        async with engine.begin() as conn:
            await conn.execute(
                update(ResearchDialogRun)
                .where(ResearchDialogRun.plan_id == plan_id)
                .values(
                    status=status,
                    error=error,
                    finished_at=now,
                )
            )

    # ── Submissions ─────────────────────────────────────────────────────

    async def record_submission(
        self,
        outcome_id: int,
        url: str,
        language: str,
        status: str,
        verdict: str,
        error: str,
    ) -> ResearchSubmission:
        engine = self._get_engine()
        async with engine.begin() as conn:
            result = await conn.execute(
                insert(ResearchSubmission)
                .values(
                    outcome_id=outcome_id,
                    url=url,
                    language=language,
                    status=status,
                    verdict=verdict,
                    error=error,
                )
                .returning(ResearchSubmission)
            )
            submission_row = result.one()
            return ResearchSubmission(**submission_row._mapping)

    async def list_submissions(
        self, outcome_id: int
    ) -> Sequence[ResearchSubmission]:
        engine = self._get_engine()
        async with engine.connect() as conn:
            result = await conn.execute(
                select(ResearchSubmission)
                .where(ResearchSubmission.outcome_id == outcome_id)
                .order_by(ResearchSubmission.created_at)
            )
            return [ResearchSubmission(**r._mapping) for r in result.all()]

    # ── Artifacts ───────────────────────────────────────────────────────

    async def record_artifact(
        self,
        run_id: str,
        local_path: str,
        content_hash: str,
        size_bytes: int,
        content_type: str,
        outcome_id: Optional[int] = None,
    ) -> ResearchArtifact:
        engine = self._get_engine()
        async with engine.begin() as conn:
            result = await conn.execute(
                insert(ResearchArtifact)
                .values(
                    run_id=run_id,
                    outcome_id=outcome_id,
                    local_path=local_path,
                    content_hash=content_hash,
                    size_bytes=size_bytes,
                    content_type=content_type,
                )
                .returning(ResearchArtifact)
            )
            artifact_row = result.one()
            return ResearchArtifact(**artifact_row._mapping)

    async def list_artifacts(self, run_id: str) -> Sequence[ResearchArtifact]:
        engine = self._get_engine()
        async with engine.connect() as conn:
            result = await conn.execute(
                select(ResearchArtifact)
                .where(ResearchArtifact.run_id == run_id)
                .order_by(ResearchArtifact.created_at)
            )
            return [ResearchArtifact(**r._mapping) for r in result.all()]

    # ── Lifecycle ───────────────────────────────────────────────────────

    async def close(self) -> None:
        """Dispose the connection pool."""
        if self._engine is not None:
            await self._engine.dispose()
            self._engine = None
