"""Research Ledger — persistent PostgreSQL storage for research runs, outcomes,
events, dialog planning, submissions, and local artifacts.

PR1 Scope:
- 7-table SQLAlchemy 2.0 DeclarativeBase schema
- AsyncEngine (asyncpg) with Core-style queries
- Alembic migration support
- BaseResearchLedgerRepository ABC + Postgres impl
- LocalArtifactStore Protocol + FileLocalArtifactStore
"""

from qwenpaw.research_ledger.schema import (
    Base,
    ResearchTask,
    ResearchRun,
    ResearchOutcome,
    ResearchEvent,
    ResearchDialogRun,
    ResearchSubmission,
    ResearchArtifact,
)
from qwenpaw.research_ledger.repository import (
    BaseResearchLedgerRepository,
    PostgresResearchLedgerRepository,
)
from qwenpaw.research_ledger.artifacts import (
    LocalArtifactStore,
    ArtifactMetadata,
    FileLocalArtifactStore,
)

__all__ = [
    "Base",
    "ResearchTask",
    "ResearchRun",
    "ResearchOutcome",
    "ResearchEvent",
    "ResearchDialogRun",
    "ResearchSubmission",
    "ResearchArtifact",
    "BaseResearchLedgerRepository",
    "PostgresResearchLedgerRepository",
    "LocalArtifactStore",
    "ArtifactMetadata",
    "FileLocalArtifactStore",
]
