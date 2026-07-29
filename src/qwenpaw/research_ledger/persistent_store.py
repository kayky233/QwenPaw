"""Persistence boundary for future database-backed Research Ledger.

The contract is separated from current in-memory repositories so runtime code
can migrate to SQLite/PostgreSQL without changing execution semantics.
"""

from __future__ import annotations

from .contracts import ResearchArtifactContract, ResearchStepContract
from .event_store import ResearchEventStore
from .step_repository import ResearchArtifactRepository, ResearchStepRepository


class ResearchLedgerStore:
    def __init__(self) -> None:
        self.steps = ResearchStepRepository()
        self.artifacts = ResearchArtifactRepository()
        self.events = ResearchEventStore()

    def add_step(self, step: ResearchStepContract) -> ResearchStepContract:
        return self.steps.create(step)

    def add_artifact(self, artifact: ResearchArtifactContract) -> ResearchArtifactContract:
        return self.artifacts.add(artifact)

    def events_for_run(self, run_id: str):
        return self.events.list_for_run(run_id)
