"""SQLite persistence backend for AutoResearch execution ledger primitives.

The table names deliberately use the ``research_execution_*`` namespace.  The
main Research Ledger already owns tables such as ``research_artifacts`` and
``research_events`` through SQLAlchemy, so reusing those names would corrupt an
existing database with an incompatible schema.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from typing import Any

from .contracts import (
    ResearchArtifactContract,
    ResearchArtifactType,
    ResearchStepContract,
    ResearchStepStatus,
    ResearchStepType,
)
from .step_events import ResearchStepEvent

_STEP_TABLE = "research_execution_steps"
_ARTIFACT_TABLE = "research_execution_artifacts"
_EVENT_TABLE = "research_execution_events"


class SQLiteResearchLedgerStore:
    """Durable store for step, artifact, and execution-event contracts."""

    def __init__(self, connection: sqlite3.Connection):
        self.conn = connection
        self.conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        self.conn.executescript(
            f"""
            CREATE TABLE IF NOT EXISTS {_STEP_TABLE} (
                step_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL,
                step_type TEXT NOT NULL,
                status TEXT NOT NULL,
                step_index INTEGER NOT NULL,
                executor TEXT NOT NULL,
                required_artifacts TEXT NOT NULL,
                input_artifacts TEXT NOT NULL,
                validation_rules TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_research_execution_steps_run
                ON {_STEP_TABLE}(run_id, step_index);

            CREATE TABLE IF NOT EXISTS {_ARTIFACT_TABLE} (
                artifact_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL,
                step_id TEXT NOT NULL,
                artifact_type TEXT NOT NULL,
                path TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                verified INTEGER NOT NULL,
                metadata TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_research_execution_artifacts_step
                ON {_ARTIFACT_TABLE}(step_id, artifact_type);
            CREATE INDEX IF NOT EXISTS idx_research_execution_artifacts_run
                ON {_ARTIFACT_TABLE}(run_id);

            CREATE TABLE IF NOT EXISTS {_EVENT_TABLE} (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                step_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                payload TEXT NOT NULL,
                timestamp TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_research_execution_events_run
                ON {_EVENT_TABLE}(run_id, id);
            """
        )
        self.conn.commit()

    @staticmethod
    def _json_array(values: tuple[Any, ...]) -> str:
        return json.dumps(
            [value.value if hasattr(value, "value") else value for value in values],
            sort_keys=True,
        )

    def save_step(self, step: ResearchStepContract) -> ResearchStepContract:
        self.conn.execute(
            f"""
            INSERT INTO {_STEP_TABLE} (
                step_id,
                run_id,
                step_type,
                status,
                step_index,
                executor,
                required_artifacts,
                input_artifacts,
                validation_rules
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(step_id) DO UPDATE SET
                run_id=excluded.run_id,
                step_type=excluded.step_type,
                status=excluded.status,
                step_index=excluded.step_index,
                executor=excluded.executor,
                required_artifacts=excluded.required_artifacts,
                input_artifacts=excluded.input_artifacts,
                validation_rules=excluded.validation_rules
            """,
            (
                step.step_id,
                step.run_id,
                step.step_type.value,
                step.status.value,
                step.index,
                step.executor,
                self._json_array(step.required_artifacts),
                self._json_array(step.input_artifacts),
                self._json_array(step.validation_rules),
            ),
        )
        self.conn.commit()
        return step

    @staticmethod
    def _decode_step(row: sqlite3.Row) -> ResearchStepContract:
        return ResearchStepContract(
            step_id=str(row["step_id"]),
            run_id=str(row["run_id"]),
            step_type=ResearchStepType(str(row["step_type"])),
            index=int(row["step_index"]),
            executor=str(row["executor"]),
            required_artifacts=tuple(
                ResearchArtifactType(value)
                for value in json.loads(str(row["required_artifacts"]))
            ),
            input_artifacts=tuple(json.loads(str(row["input_artifacts"]))),
            validation_rules=tuple(json.loads(str(row["validation_rules"]))),
            status=ResearchStepStatus(str(row["status"])),
        )

    def get_step(self, step_id: str) -> ResearchStepContract | None:
        row = self.conn.execute(
            f"SELECT * FROM {_STEP_TABLE} WHERE step_id=?",
            (step_id,),
        ).fetchone()
        return self._decode_step(row) if row is not None else None

    def list_steps(self, run_id: str) -> list[ResearchStepContract]:
        rows = self.conn.execute(
            f"""
            SELECT * FROM {_STEP_TABLE}
            WHERE run_id=?
            ORDER BY step_index ASC, step_id ASC
            """,
            (run_id,),
        ).fetchall()
        return [self._decode_step(row) for row in rows]

    def save_artifact(
        self,
        artifact: ResearchArtifactContract,
    ) -> ResearchArtifactContract:
        self.conn.execute(
            f"""
            INSERT INTO {_ARTIFACT_TABLE} (
                artifact_id,
                run_id,
                step_id,
                artifact_type,
                path,
                content_hash,
                verified,
                metadata
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(artifact_id) DO UPDATE SET
                run_id=excluded.run_id,
                step_id=excluded.step_id,
                artifact_type=excluded.artifact_type,
                path=excluded.path,
                content_hash=excluded.content_hash,
                verified=excluded.verified,
                metadata=excluded.metadata
            """,
            (
                artifact.artifact_id,
                artifact.run_id,
                artifact.step_id,
                artifact.artifact_type.value,
                artifact.path,
                artifact.content_hash,
                int(artifact.verified),
                json.dumps(artifact.metadata, sort_keys=True),
            ),
        )
        self.conn.commit()
        return artifact

    @staticmethod
    def _decode_artifact(row: sqlite3.Row) -> ResearchArtifactContract:
        return ResearchArtifactContract(
            artifact_id=str(row["artifact_id"]),
            run_id=str(row["run_id"]),
            step_id=str(row["step_id"]),
            artifact_type=ResearchArtifactType(str(row["artifact_type"])),
            path=str(row["path"]),
            content_hash=str(row["content_hash"]),
            verified=bool(row["verified"]),
            metadata=dict(json.loads(str(row["metadata"]))),
        )

    def get_artifact(self, artifact_id: str) -> ResearchArtifactContract | None:
        row = self.conn.execute(
            f"SELECT * FROM {_ARTIFACT_TABLE} WHERE artifact_id=?",
            (artifact_id,),
        ).fetchone()
        return self._decode_artifact(row) if row is not None else None

    def list_artifacts(
        self,
        *,
        run_id: str | None = None,
        step_id: str | None = None,
    ) -> list[ResearchArtifactContract]:
        if (run_id is None) == (step_id is None):
            raise ValueError("provide exactly one of run_id or step_id")
        column = "run_id" if run_id is not None else "step_id"
        value = run_id if run_id is not None else step_id
        rows = self.conn.execute(
            f"""
            SELECT * FROM {_ARTIFACT_TABLE}
            WHERE {column}=?
            ORDER BY artifact_id ASC
            """,
            (value,),
        ).fetchall()
        return [self._decode_artifact(row) for row in rows]

    def append_event(self, event: ResearchStepEvent) -> ResearchStepEvent:
        self.conn.execute(
            f"""
            INSERT INTO {_EVENT_TABLE} (
                run_id,
                step_id,
                event_type,
                payload,
                timestamp
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                event.run_id,
                event.step_id,
                event.event_type,
                json.dumps(event.payload, sort_keys=True),
                event.timestamp,
            ),
        )
        self.conn.commit()
        return event

    def list_events(self, run_id: str) -> list[ResearchStepEvent]:
        rows = self.conn.execute(
            f"SELECT * FROM {_EVENT_TABLE} WHERE run_id=? ORDER BY id ASC",
            (run_id,),
        ).fetchall()
        return [
            ResearchStepEvent(
                event_type=str(row["event_type"]),
                run_id=str(row["run_id"]),
                step_id=str(row["step_id"]),
                timestamp=str(row["timestamp"]),
                payload=dict(json.loads(str(row["payload"]))),
            )
            for row in rows
        ]

    def recover_interrupted_steps(
        self,
        run_id: str,
    ) -> list[ResearchStepContract]:
        """Move persisted RUNNING steps to BLOCKED after process restart."""

        recovered: list[ResearchStepContract] = []
        for step in self.list_steps(run_id):
            if step.status != ResearchStepStatus.RUNNING:
                continue
            blocked = replace(step, status=ResearchStepStatus.BLOCKED)
            self.save_step(blocked)
            self.append_event(
                ResearchStepEvent.create(
                    event_type="step_recovered",
                    run_id=run_id,
                    step_id=step.step_id,
                    payload={
                        "previous_status": ResearchStepStatus.RUNNING.value,
                        "recovered_status": ResearchStepStatus.BLOCKED.value,
                        "reason": "runtime_interrupted_requires_review",
                    },
                )
            )
            recovered.append(blocked)
        return recovered

    def count_steps(self, run_id: str) -> int:
        row = self.conn.execute(
            f"SELECT COUNT(*) FROM {_STEP_TABLE} WHERE run_id=?",
            (run_id,),
        ).fetchone()
        return int(row[0])
