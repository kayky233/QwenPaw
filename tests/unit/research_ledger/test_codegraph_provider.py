import pytest

from qwenpaw.research_ledger.codegraph_provider import CodeGraphProvider, GraphStaleError
from qwenpaw.research_ledger.graph_snapshot import GraphSnapshot, GraphSnapshotStatus


class Backend:
    def __init__(self, revision="abc"):
        self.revision = revision

    def snapshot(self):
        return GraphSnapshot(
            repository="owner/repo",
            revision=self.revision,
            schema_version="1",
            indexed_at="2026-07-29T00:00:00Z",
            node_count=10,
            edge_count=20,
            status=GraphSnapshotStatus.READY,
        )

    def search(self, query, limit):
        return [{
            "node_id": "n1",
            "kind": "class",
            "name": "CacheManager",
            "path": "src/cache.py",
        }]

    def neighbors(self, node_id, depth):
        return []

    def find_tests(self, paths):
        return []


def test_codegraph_provider_rejects_stale_revision():
    provider = CodeGraphProvider(Backend("old"), expected_revision="new")
    with pytest.raises(GraphStaleError):
        provider.search_symbols("CacheManager")


def test_codegraph_provider_returns_nodes_for_matching_revision():
    provider = CodeGraphProvider(Backend("abc"), expected_revision="abc")
    assert provider.search_symbols("CacheManager")[0].path == "src/cache.py"
