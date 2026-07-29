from qwenpaw.research_ledger.context_pack import RepositoryContextPackBuilder
from qwenpaw.research_ledger.issue_context_planner import ContextualIssueSolverPlanner
from qwenpaw.research_ledger.issue_solver import IssueTask
from qwenpaw.research_ledger.repository_analyzer import RepositoryAnalyzer
from qwenpaw.research_ledger.repository_graph import RepositoryGraphNode


class FakeGraphProvider:
    def search_symbols(self, query: str, limit: int = 20):
        if "CacheManager" in query or "src/memory/cache.py" in query:
            return [
                RepositoryGraphNode(
                    node_id="cache",
                    kind="class",
                    name="CacheManager",
                    path="src/memory/cache.py",
                    line=10,
                    summary="Manages memory cache entries.",
                )
            ]
        return []

    def neighbors(self, node_id: str, depth: int = 1):
        return []

    def find_tests(self, paths: tuple[str, ...]):
        return [
            RepositoryGraphNode(
                node_id="cache-test",
                kind="test",
                name="test_cache_manager",
                path="tests/memory/test_cache.py",
                summary="Covers CacheManager behavior.",
            )
        ] if paths else []


def test_repository_analyzer_extracts_paths_and_symbols():
    analyzer = RepositoryAnalyzer(FakeGraphProvider())
    result = analyzer.analyze(
        "`CacheManager` fails in src/memory/cache.py with error: cache miss"
    )
    assert result.graph_available
    assert result.affected_paths == ("src/memory/cache.py",)
    assert result.test_nodes[0].path == "tests/memory/test_cache.py"


def test_context_pack_respects_budget():
    analysis = RepositoryAnalyzer(FakeGraphProvider()).analyze("`CacheManager`")
    pack = RepositoryContextPackBuilder(token_budget=30).build(analysis)
    assert pack.estimated_tokens <= 30
    assert pack.graph_available


def test_issue_planner_includes_repository_context():
    planner = ContextualIssueSolverPlanner(
        RepositoryAnalyzer(FakeGraphProvider()),
        RepositoryContextPackBuilder(token_budget=100),
    )
    result = planner.create_plan(
        IssueTask(
            repository="owner/repo",
            issue_number=1,
            title="Fix CacheManager",
            description="`CacheManager` fails in src/memory/cache.py",
        )
    )
    assert result.context_pack.affected_paths == ("src/memory/cache.py",)
    assert result.impact_set.source_paths == ("src/memory/cache.py",)
    assert result.selected_tests[0].path == "tests/memory/test_cache.py"
