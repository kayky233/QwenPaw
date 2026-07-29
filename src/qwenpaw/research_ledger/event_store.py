"""Small event store abstraction for Research Ledger integration."""

from __future__ import annotations

from .step_events import ResearchStepEvent


class ResearchEventStore:
    def __init__(self) -> None:
        self._events: list[ResearchStepEvent] = []

    def append(self, event: ResearchStepEvent) -> ResearchStepEvent:
        self._events.append(event)
        return event

    def list_for_run(self, run_id: str) -> list[ResearchStepEvent]:
        return [item for item in self._events if item.run_id == run_id]

    def latest(self, run_id: str) -> ResearchStepEvent | None:
        events = self.list_for_run(run_id)
        return events[-1] if events else None
