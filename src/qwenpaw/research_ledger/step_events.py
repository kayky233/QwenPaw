"""Events emitted by durable AutoResearch step execution."""

from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Any


@dataclass(frozen=True)
class ResearchStepEvent:
    event_type: str
    run_id: str
    step_id: str
    timestamp: str
    payload: dict[str, Any]

    @classmethod
    def create(
        cls,
        event_type: str,
        run_id: str,
        step_id: str,
        payload: dict[str, Any] | None = None,
    ) -> "ResearchStepEvent":
        return cls(
            event_type=event_type,
            run_id=run_id,
            step_id=step_id,
            timestamp=datetime.now(timezone.utc).isoformat(),
            payload=payload or {},
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


STEP_STARTED = "step_started"
STEP_COMPLETED = "step_completed"
STEP_FAILED = "step_failed"
STEP_BLOCKED = "step_blocked"
ARTIFACT_CREATED = "artifact_created"
ARTIFACT_VERIFIED = "artifact_verified"
