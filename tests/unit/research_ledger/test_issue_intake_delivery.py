import pytest

from qwenpaw.research_ledger.change_request_delivery import (
    ChangeRequest,
    ChangeRequestResult,
    EvidenceGatedDelivery,
)
from qwenpaw.research_ledger.context_pack import RepositoryContextPackBuilder
from qwenpaw.research_ledger.contracts import (
    ResearchArtifactContract,
    ResearchArtifactType,
)
from qwenpaw.research_ledger.issue_context_planner import ContextualIssueSolverPlanner
from qwenpaw.research_ledger.issue_fetcher import GitHubIssueFetcher
from qwenpaw.research_ledger.issue_solver_service import IssueSolverService
from qwenpaw.research_ledger.repository_analyzer import RepositoryAnalyzer
from qwenpaw.research_ledger.repository_graph import NoopRepositoryGraphProvider
from qwenpaw.research_ledger.step_repository import ResearchArtifactRepository


class Client:
    def get_issue(self, repository, number):
        return {
            "title": "Fix cache",
            "body": "Cache fails",
            "state": "open",
            "labels": ["bug"],
        }

    def get_issue_comments(self, repository, number):
        return [{"author": "alice", "body": "See `CacheManager`"}]


class Provider:
    def create(self, request):
        return ChangeRequestResult("https://example.test/pr/1", 1)


def test_issue_fetcher_includes_comments():
    evidence = GitHubIssueFetcher(Client()).fetch_issue("owner/repo", 1)
    assert evidence.comments[0].author == "alice"


def test_issue_solver_service_prepares_contextual_plan():
    service = IssueSolverService(
        GitHubIssueFetcher(Client()),
        ContextualIssueSolverPlanner(
            RepositoryAnalyzer(NoopRepositoryGraphProvider()),
            RepositoryContextPackBuilder(),
        ),
    )
    result = service.prepare("owner/repo", 1)
    assert result.evidence.title == "Fix cache"
    assert result.contextual_plan.context_pack.graph_available is False


def test_delivery_requires_verified_evidence():
    artifacts = ResearchArtifactRepository()
    delivery = EvidenceGatedDelivery(artifacts, Provider())
    request = ChangeRequest("owner/repo", "main", "feature", "title", "body")
    with pytest.raises(RuntimeError):
        delivery.create("delivery", request)

    for artifact_type in EvidenceGatedDelivery.REQUIRED:
        artifacts.add(
            ResearchArtifactContract(
                artifact_id=artifact_type.value,
                run_id="run",
                step_id="delivery",
                artifact_type=artifact_type,
                path=artifact_type.value,
                content_hash="0" * 64,
                verified=True,
            )
        )
    assert delivery.create("delivery", request).number == 1
