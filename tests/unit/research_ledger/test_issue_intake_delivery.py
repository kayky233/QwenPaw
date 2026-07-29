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
    def __init__(self):
        self.requests = []

    def create(self, request):
        self.requests.append(request)
        return ChangeRequestResult("https://example.test/pr/1", 1)


def _artifact(artifact_type, *, step_id="delivery", run_id="run"):
    return ResearchArtifactContract(
        artifact_id=f"{step_id}-{artifact_type.value}",
        run_id=run_id,
        step_id=step_id,
        artifact_type=artifact_type,
        path=artifact_type.value,
        content_hash="0" * 64,
        verified=True,
    )


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
    provider = Provider()
    delivery = EvidenceGatedDelivery(artifacts, provider)
    request = ChangeRequest("owner/repo", "main", "feature", "title", "body")
    with pytest.raises(RuntimeError):
        delivery.create("delivery", request)

    for artifact_type in EvidenceGatedDelivery.REQUIRED:
        artifacts.add(_artifact(artifact_type))

    assert delivery.create("delivery", request).number == 1
    assert provider.requests == [request]


def test_run_delivery_accepts_evidence_from_distinct_steps():
    artifacts = ResearchArtifactRepository()
    provider = Provider()
    delivery = EvidenceGatedDelivery(artifacts, provider)
    request = ChangeRequest("owner/repo", "main", "feature", "title", "body")
    step_by_type = {
        ResearchArtifactType.CODE_DIFF: "implement",
        ResearchArtifactType.TEST_RESULT: "test",
        ResearchArtifactType.REPORT: "review",
        ResearchArtifactType.COMMIT: "delivery",
    }
    for artifact_type, step_id in step_by_type.items():
        artifacts.add(_artifact(artifact_type, step_id=step_id))

    result = delivery.create_for_run("run", request)

    assert result.number == 1
    assert artifacts.all_verified_for_run(
        "run",
        EvidenceGatedDelivery.REQUIRED,
    )


def test_run_delivery_rejects_evidence_from_other_run():
    artifacts = ResearchArtifactRepository()
    provider = Provider()
    delivery = EvidenceGatedDelivery(artifacts, provider)
    request = ChangeRequest("owner/repo", "main", "feature", "title", "body")
    for artifact_type in EvidenceGatedDelivery.REQUIRED:
        artifacts.add(
            _artifact(
                artifact_type,
                step_id=artifact_type.value,
                run_id="different-run",
            )
        )

    with pytest.raises(RuntimeError, match="code_diff"):
        delivery.create_for_run("run", request)

    assert provider.requests == []
