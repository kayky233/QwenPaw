import pytest

from qwenpaw.research_ledger.context_pack import RepositoryContextPack
from qwenpaw.research_ledger.contracts import ResearchArtifactType
from qwenpaw.research_ledger.episode_builder import IssueEpisodeBuilder
from qwenpaw.research_ledger.episode_package import (
    EpisodeCommand,
    EpisodeExpectedArtifact,
    EpisodePackage,
)
from qwenpaw.research_ledger.issue_context_planner import ContextualIssueSolvePlan
from qwenpaw.research_ledger.issue_fetcher import IssueEvidence
from qwenpaw.research_ledger.issue_solver import IssueSolvePlan, IssueTask
from qwenpaw.research_ledger.issue_solver_service import PreparedIssueSolve
from qwenpaw.research_ledger.repository_analyzer import RepositoryAnalysis


def _package(**overrides):
    values = {
        "episode_id": "ep-1",
        "run_id": "run-1",
        "task_type": "bug_fix",
        "repository": "owner/repo",
        "issue_number": 12,
        "goal": "Fix cache expiry",
        "base_revision": "abc123",
        "acceptance_criteria": ("expired entries are not returned",),
        "modifiable_files": (
            "src/qwenpaw/memory/cache.py",
            "tests/unit/test_cache.py",
        ),
        "frozen_files": ("pyproject.toml",),
        "commands": (
            EpisodeCommand(
                "unit",
                "unit",
                ("pytest", "-q", "tests/unit/test_cache.py"),
            ),
        ),
        "expected_artifacts": (
            EpisodeExpectedArtifact(
                ResearchArtifactType.CODE_DIFF,
                "implement",
            ),
            EpisodeExpectedArtifact(
                ResearchArtifactType.TEST_RESULT,
                "test",
            ),
        ),
        "environment": {"platform": "linux"},
    }
    values.update(overrides)
    return EpisodePackage(**values)


def test_episode_package_round_trip_and_digest_are_deterministic():
    package = _package()

    restored = EpisodePackage.from_json(package.to_json())

    assert restored == package
    assert restored.digest() == package.digest()
    assert len(package.digest()) == 64


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"commands": ()}, "validation command"),
        ({"expected_artifacts": ()}, "expected artifact"),
        (
            {"frozen_files": ("src/qwenpaw/memory/cache.py",)},
            "overlap",
        ),
        (
            {"modifiable_files": ("src/**/cache.py",)},
            "wildcards",
        ),
        (
            {
                "commands": (
                    EpisodeCommand("unit", "unit", ("pytest",)),
                    EpisodeCommand("unit", "lint", ("ruff", "check", ".")),
                )
            },
            "duplicate command_id",
        ),
    ],
)
def test_episode_package_rejects_non_reproducible_contracts(overrides, message):
    with pytest.raises(ValueError, match=message):
        _package(**overrides).validate()


def _prepared_issue(paths=("src/qwenpaw/memory/cache.py",)):
    issue = IssueTask(
        repository="owner/repo",
        issue_number=12,
        title="Fix cache expiry",
        description="expired cache entries remain visible",
    )
    context = RepositoryContextPack(
        items=(),
        affected_paths=paths,
        test_paths=("tests/unit/test_cache.py",) if paths else (),
        estimated_tokens=25,
        truncated=False,
        graph_available=bool(paths),
        downgrade_reason=None if paths else "no_matches",
    )
    contextual = ContextualIssueSolvePlan(
        plan=IssueSolvePlan(
            issue=issue,
            analysis="localized cache expiry",
            implementation_steps=("update cache",),
            validation_steps=("run cache tests",),
        ),
        analysis=RepositoryAnalysis(
            anchors=("cache",),
            candidate_nodes=(),
            affected_paths=paths,
            test_nodes=(),
            graph_available=bool(paths),
            downgrade_reason=None if paths else "no_matches",
        ),
        context_pack=context,
    )
    return PreparedIssueSolve(
        evidence=IssueEvidence(
            repository="owner/repo",
            number=12,
            title="Fix cache expiry",
            body="expired cache entries remain visible",
            state="open",
            labels=("bug",),
        ),
        contextual_plan=contextual,
    )


def test_issue_episode_builder_uses_localized_scope_and_metadata():
    package = IssueEpisodeBuilder().build(
        _prepared_issue(),
        run_id="run-1",
        base_revision="abc123",
        task_type="bug_fix",
        acceptance_criteria=("expired entries are hidden",),
        commands=(
            EpisodeCommand(
                "unit",
                "unit",
                ("pytest", "-q", "tests/unit/test_cache.py"),
            ),
        ),
    )

    assert package.modifiable_files == (
        "src/qwenpaw/memory/cache.py",
        "tests/unit/test_cache.py",
    )
    assert package.metadata["graph_available"] is True
    assert package.metadata["issue_labels"] == ["bug"]
    assert package.expected_artifacts[-1].artifact_type == ResearchArtifactType.COMMIT


def test_issue_episode_builder_requires_safe_localized_scope():
    with pytest.raises(ValueError, match="no modifiable paths"):
        IssueEpisodeBuilder().build(
            _prepared_issue(paths=()),
            run_id="run-1",
            base_revision="abc123",
            task_type="bug_fix",
            acceptance_criteria=("issue resolved",),
            commands=(EpisodeCommand("unit", "unit", ("pytest",)),),
        )
