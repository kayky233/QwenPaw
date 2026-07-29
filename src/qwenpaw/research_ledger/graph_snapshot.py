"""Versioned repository graph snapshots for deterministic analysis."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class GraphSnapshotStatus(str, Enum):
    READY = "ready"
    STALE = "stale"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class GraphSnapshot:
    repository: str
    revision: str
    schema_version: str
    indexed_at: str
    node_count: int
    edge_count: int
    status: GraphSnapshotStatus = GraphSnapshotStatus.READY

    def is_usable_for(self, revision: str) -> bool:
        return self.status == GraphSnapshotStatus.READY and self.revision == revision
