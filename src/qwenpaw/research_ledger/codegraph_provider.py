"""Adapter from a CodeGraph backend to repository graph contracts."""

from __future__ import annotations

from .graph_snapshot import GraphSnapshot
from .repository_graph import RepositoryGraphNode


class GraphStaleError(RuntimeError):
    pass


class CodeGraphProvider:
    """Wrap an injected CodeGraph backend.

    Expected backend methods: ``snapshot()``, ``search(query, limit)``,
    ``neighbors(node_id, depth)``, and ``find_tests(paths)``.
    """

    def __init__(self, backend, expected_revision: str | None = None):
        self.backend = backend
        self.expected_revision = expected_revision

    def snapshot(self) -> GraphSnapshot:
        raw = self.backend.snapshot()
        snapshot = raw if isinstance(raw, GraphSnapshot) else GraphSnapshot(**raw)
        if self.expected_revision and not snapshot.is_usable_for(self.expected_revision):
            raise GraphStaleError(
                f"repository graph revision {snapshot.revision!r} does not match "
                f"expected revision {self.expected_revision!r}"
            )
        return snapshot

    @staticmethod
    def _node(raw) -> RepositoryGraphNode:
        return raw if isinstance(raw, RepositoryGraphNode) else RepositoryGraphNode(**raw)

    def search_symbols(self, query: str, limit: int = 20) -> list[RepositoryGraphNode]:
        self.snapshot()
        return [self._node(item) for item in self.backend.search(query, limit)]

    def neighbors(self, node_id: str, depth: int = 1) -> list[RepositoryGraphNode]:
        self.snapshot()
        return [self._node(item) for item in self.backend.neighbors(node_id, depth)]

    def find_tests(self, paths: tuple[str, ...]) -> list[RepositoryGraphNode]:
        self.snapshot()
        return [self._node(item) for item in self.backend.find_tests(paths)]
