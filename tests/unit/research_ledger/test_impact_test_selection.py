from qwenpaw.research_ledger.graph_test_selector import GraphTestSelector
from qwenpaw.research_ledger.impact_analysis import ImpactSetBuilder
from qwenpaw.research_ledger.repository_analyzer import RepositoryAnalysis
from qwenpaw.research_ledger.repository_graph import RepositoryGraphNode


def test_impact_set_and_test_selection():
    analysis = RepositoryAnalysis(
        anchors=("CacheManager",),
        candidate_nodes=(
            RepositoryGraphNode("n1", "class", "CacheManager", "src/cache.py"),
        ),
        affected_paths=("src/cache.py",),
        test_nodes=(
            RepositoryGraphNode("t1", "test", "test_cache", "tests/test_cache.py"),
        ),
        graph_available=True,
    )
    impact = ImpactSetBuilder().build(analysis)
    selected = GraphTestSelector().select(impact)
    assert impact.source_paths == ("src/cache.py",)
    assert selected[0].path == "tests/test_cache.py"
