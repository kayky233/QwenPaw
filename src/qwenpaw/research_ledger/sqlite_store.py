"""SQLite persistence backend for AutoResearch ledger primitives."""

from __future__ import annotations

import json
import sqlite3

from .contracts import ResearchArtifactContract, ResearchStepContract


class SQLiteResearchLedgerStore:
    def __init__(self, connection: sqlite3.Connection):
        self.conn = connection
        self._init_schema()

    def _init_schema(self) -> None:
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS research_steps (
                step_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL,
                step_type TEXT NOT NULL,
                status TEXT NOT NULL,
                step_index INTEGER NOT NULL,
                payload TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS research_artifacts (
                artifact_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL,
                step_id TEXT NOT NULL,
                artifact_type TEXT NOT NULL,
                path TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                verified INTEGER NOT NULL,
                metadata TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS research_step_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                step_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                payload TEXT NOT NULL,
                timestamp TEXT NOT NULL
            );
            """
        )
        self.conn.commit()

    def save_step(self, step: ResearchStepContract) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO research_steps VALUES (?, ?, ?, ?, ?, ?)",
            (
                step.step_id,
                step.run_id,
                step.step_type.value,
                step.status.value,
                step.index,
                json.dumps(step.__dict__, default=str),
            ),
        )
        self.conn.commit()

    def save_artifact(self, artifact: ResearchArtifactContract) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO research_artifacts VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                artifact.artifact_id,
                artifact.run_id,
                artifact.step_id,
                artifact.artifact_type.value,
                artifact.path,
                artifact.content_hash,
                int(artifact.verified),
                json.dumps(artifact.metadata),
            ),
        )
        self.conn.commit()

    def count_steps(self, run_id: str) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) FROM research_steps WHERE run_id=?",
            (run_id,),
        ).fetchone()
        return int(row[0])
