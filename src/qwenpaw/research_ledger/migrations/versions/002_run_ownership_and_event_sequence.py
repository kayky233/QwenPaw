"""Persist run ownership and event sequence.

Revision ID: 002
Revises: 001
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "research_runs",
        sa.Column(
            "owner_agent_id",
            sa.String(128),
            nullable=False,
            server_default="default",
        ),
    )
    op.add_column(
        "research_runs",
        sa.Column("owner_user_id", sa.String(128), nullable=True),
    )
    op.add_column(
        "research_runs",
        sa.Column("owner_session_id", sa.String(128), nullable=True),
    )
    op.add_column(
        "research_events",
        sa.Column("sequence", sa.Integer(), nullable=True),
    )
    op.execute(
        """
        WITH ranked AS (
            SELECT id, ROW_NUMBER() OVER (
                PARTITION BY run_id ORDER BY timestamp, id
            ) - 1 AS sequence
            FROM research_events
        )
        UPDATE research_events
        SET sequence = ranked.sequence
        FROM ranked
        WHERE research_events.id = ranked.id
        """
    )
    op.alter_column("research_events", "sequence", nullable=False)
    op.create_unique_constraint(
        "uq_research_event_run_sequence",
        "research_events",
        ["run_id", "sequence"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_research_event_run_sequence",
        "research_events",
        type_="unique",
    )
    op.drop_column("research_events", "sequence")
    op.drop_column("research_runs", "owner_session_id")
    op.drop_column("research_runs", "owner_user_id")
    op.drop_column("research_runs", "owner_agent_id")
