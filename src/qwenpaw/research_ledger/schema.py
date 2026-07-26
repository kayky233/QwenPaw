"""SQLAlchemy 2.0 ORM schema for the Research Ledger — 7 tables.

Uses DeclarativeBase with Mapped[] annotations and asyncpg as the async driver.
Core-style query patterns (insert/select/update) are used in the repository layer.

Tables:
  1. research_tasks        — Immutable task definitions (program, judge, solution)
  2. research_runs         — Run lifecycle (status, phase, rounds, timestamps)
  3. research_outcomes     — Per-round evaluation results (scores, metrics, sources)
  4. research_events       — Timeline events (phase transitions, errors)
  5. research_dialog_runs  — Dialog-based planning runs
  6. research_submissions  — External benchmark/LeetCode submissions
  7. research_artifacts    — Local artifact file metadata
"""

from datetime import datetime
from typing import Optional
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Base for all Research Ledger ORM models."""
    pass


# ── 1. research_tasks ──────────────────────────────────────────────────────

class ResearchTask(Base):
    """Immutable task definition: program spec, judge, and reference solution."""

    __tablename__ = "research_tasks"

    task_id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: uuid4().hex[:12]
    )
    program: Mapped[str] = mapped_column(Text, nullable=False)
    solution_name: Mapped[str] = mapped_column(String(32), nullable=False)
    solution_source: Mapped[str] = mapped_column(Text, nullable=False)
    judge_source: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # Relationships
    runs: Mapped[list["ResearchRun"]] = relationship(
        back_populates="task", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"ResearchTask(task_id={self.task_id!r}, solution_name={self.solution_name!r})"


# ── 2. research_runs ───────────────────────────────────────────────────────

class ResearchRun(Base):
    """A single research run with lifecycle tracking."""

    __tablename__ = "research_runs"

    run_id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: uuid4().hex[:12]
    )
    task_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("research_tasks.task_id", ondelete="CASCADE"), nullable=False
    )
    agent_id: Mapped[str] = mapped_column(String(128), nullable=False)
    owner_agent_id: Mapped[str] = mapped_column(String(128), nullable=False)
    owner_user_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    owner_session_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="running"
    )
    phase: Mapped[str] = mapped_column(
        String(32), nullable=False, default="running"
    )
    rounds: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completed_rounds: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    current_round: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    error: Mapped[str] = mapped_column(Text, nullable=False, default="")
    research_brief: Mapped[str] = mapped_column(
        Text, nullable=False, default="{}"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    started_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    finished_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Relationships
    task: Mapped["ResearchTask"] = relationship(back_populates="runs")
    outcomes: Mapped[list["ResearchOutcome"]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )
    events: Mapped[list["ResearchEvent"]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )
    artifacts: Mapped[list["ResearchArtifact"]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("run_id", name="uq_research_runs_run_id"),
    )

    def __repr__(self) -> str:
        return (
            f"ResearchRun(run_id={self.run_id!r}, task_id={self.task_id!r}, "
            f"status={self.status!r}, phase={self.phase!r})"
        )


# ── 3. research_outcomes ───────────────────────────────────────────────────

class ResearchOutcome(Base):
    """Per-round evaluation result: pass/fail, scores, metrics, and source code."""

    __tablename__ = "research_outcomes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("research_runs.run_id", ondelete="CASCADE"), nullable=False
    )
    round: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False
    )  # "kept" / "rejected" / "proposer_error"
    passed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    baseline_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    candidate_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    improvement: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    metrics: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    error: Mapped[str] = mapped_column(Text, nullable=False, default="")
    parent_source: Mapped[str] = mapped_column(Text, nullable=False, default="")
    candidate_source: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # Relationships
    run: Mapped["ResearchRun"] = relationship(back_populates="outcomes")
    submissions: Mapped[list["ResearchSubmission"]] = relationship(
        back_populates="outcome", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("run_id", "round", name="uq_outcome_run_round"),
    )

    def __repr__(self) -> str:
        return (
            f"ResearchOutcome(run_id={self.run_id!r}, round={self.round!r}, "
            f"status={self.status!r}, passed={self.passed})"
        )


# ── 4. research_events ─────────────────────────────────────────────────────

class ResearchEvent(Base):
    """Immutable timeline event for a research run."""

    __tablename__ = "research_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("research_runs.run_id", ondelete="CASCADE"), nullable=False
    )
    phase: Mapped[str] = mapped_column(String(32), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    round: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    detail: Mapped[str] = mapped_column(Text, nullable=False, default="{}")

    # Relationships
    run: Mapped["ResearchRun"] = relationship(back_populates="events")

    __table_args__ = (
        UniqueConstraint(
            "run_id",
            "sequence",
            name="uq_research_event_run_sequence",
        ),
    )

    def __repr__(self) -> str:
        return (
            f"ResearchEvent(run_id={self.run_id!r}, phase={self.phase!r}, "
            f"round={self.round})"
        )


# ── 5. research_dialog_runs ────────────────────────────────────────────────

class ResearchDialogRun(Base):
    """Dialog-based planning run (parallel SSE stream for plan dialog)."""

    __tablename__ = "research_dialog_runs"

    plan_id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: uuid4().hex[:12]
    )
    task_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("research_tasks.task_id", ondelete="SET NULL"), nullable=True
    )
    run_id: Mapped[Optional[str]] = mapped_column(
        String(64),
        ForeignKey("research_runs.run_id", ondelete="SET NULL"),
        nullable=True,
    )
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="planning"
    )  # "planning" / "planned" / "failed" / "cancelled"
    goal: Mapped[str] = mapped_column(Text, nullable=False, default="")
    rounds: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    started_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    finished_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    def __repr__(self) -> str:
        return (
            f"ResearchDialogRun(plan_id={self.plan_id!r}, status={self.status!r})"
        )


# ── 6. research_submissions ────────────────────────────────────────────────

class ResearchSubmission(Base):
    """External benchmark/LeetCode submission associated with an outcome."""

    __tablename__ = "research_submissions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    outcome_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("research_outcomes.id", ondelete="CASCADE"), nullable=False
    )
    url: Mapped[str] = mapped_column(String(1024), nullable=False, default="")
    language: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="pending"
    )  # "pending" / "submitted" / "accepted" / "failed"
    verdict: Mapped[str] = mapped_column(Text, nullable=False, default="")
    error: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # Relationships
    outcome: Mapped["ResearchOutcome"] = relationship(back_populates="submissions")

    def __repr__(self) -> str:
        return (
            f"ResearchSubmission(outcome_id={self.outcome_id}, "
            f"status={self.status!r})"
        )


# ── 7. research_artifacts ──────────────────────────────────────────────────

class ResearchArtifact(Base):
    """Local artifact file metadata (source dumps, metrics JSON, logs)."""

    __tablename__ = "research_artifacts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("research_runs.run_id", ondelete="CASCADE"), nullable=False
    )
    outcome_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("research_outcomes.id", ondelete="SET NULL"), nullable=True
    )
    local_path: Mapped[str] = mapped_column(String(2048), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    content_type: Mapped[str] = mapped_column(String(64), nullable=False, default="application/octet-stream")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # Relationships
    run: Mapped["ResearchRun"] = relationship(back_populates="artifacts")

    __table_args__ = (
        UniqueConstraint("run_id", "local_path", name="uq_artifact_run_path"),
    )

    def __repr__(self) -> str:
        return (
            f"ResearchArtifact(run_id={self.run_id!r}, "
            f"local_path={self.local_path!r})"
        )
