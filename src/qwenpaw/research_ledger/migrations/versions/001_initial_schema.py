"""Initial Research Ledger schema — 7 tables

Revision ID: 001
Revises:
Create Date: 2026-07-17
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. research_tasks
    op.create_table(
        "research_tasks",
        sa.Column("task_id", sa.String(64), primary_key=True),
        sa.Column("program", sa.Text(), nullable=False),
        sa.Column("solution_name", sa.String(32), nullable=False),
        sa.Column("solution_source", sa.Text(), nullable=False),
        sa.Column("judge_source", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )

    # 2. research_runs
    op.create_table(
        "research_runs",
        sa.Column("run_id", sa.String(64), primary_key=True),
        sa.Column(
            "task_id",
            sa.String(64),
            sa.ForeignKey("research_tasks.task_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("agent_id", sa.String(128), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="running"),
        sa.Column("phase", sa.String(32), nullable=False, server_default="running"),
        sa.Column("rounds", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("completed_rounds", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("current_round", sa.Integer(), nullable=True),
        sa.Column("error", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("run_id", name="uq_research_runs_run_id"),
    )

    # 3. research_outcomes
    op.create_table(
        "research_outcomes",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "run_id",
            sa.String(64),
            sa.ForeignKey("research_runs.run_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("round", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("passed", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("baseline_score", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("candidate_score", sa.Float(), nullable=True),
        sa.Column("improvement", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("metrics", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("error", sa.Text(), nullable=False, server_default=""),
        sa.Column("parent_source", sa.Text(), nullable=False, server_default=""),
        sa.Column("candidate_source", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("run_id", "round", name="uq_outcome_run_round"),
    )

    # 4. research_events
    op.create_table(
        "research_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "run_id",
            sa.String(64),
            sa.ForeignKey("research_runs.run_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("phase", sa.String(32), nullable=False),
        sa.Column(
            "timestamp",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("round", sa.Integer(), nullable=True),
        sa.Column("detail", sa.Text(), nullable=False, server_default="{}"),
    )

    # 5. research_dialog_runs
    op.create_table(
        "research_dialog_runs",
        sa.Column("plan_id", sa.String(64), primary_key=True),
        sa.Column(
            "task_id",
            sa.String(64),
            sa.ForeignKey("research_tasks.task_id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "run_id",
            sa.String(64),
            sa.ForeignKey("research_runs.run_id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("status", sa.String(32), nullable=False, server_default="planning"),
        sa.Column("goal", sa.Text(), nullable=False, server_default=""),
        sa.Column("rounds", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )

    # 6. research_submissions
    op.create_table(
        "research_submissions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "outcome_id",
            sa.Integer(),
            sa.ForeignKey("research_outcomes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("url", sa.String(1024), nullable=False, server_default=""),
        sa.Column("language", sa.String(32), nullable=False, server_default=""),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("verdict", sa.Text(), nullable=False, server_default=""),
        sa.Column("error", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )

    # 7. research_artifacts
    op.create_table(
        "research_artifacts",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "run_id",
            sa.String(64),
            sa.ForeignKey("research_runs.run_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "outcome_id",
            sa.Integer(),
            sa.ForeignKey("research_outcomes.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("local_path", sa.String(2048), nullable=False),
        sa.Column("content_hash", sa.String(128), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "content_type",
            sa.String(64),
            nullable=False,
            server_default="application/octet-stream",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("run_id", "local_path", name="uq_artifact_run_path"),
    )


def downgrade() -> None:
    op.drop_table("research_artifacts")
    op.drop_table("research_submissions")
    op.drop_table("research_dialog_runs")
    op.drop_table("research_events")
    op.drop_table("research_outcomes")
    op.drop_table("research_runs")
    op.drop_table("research_tasks")
