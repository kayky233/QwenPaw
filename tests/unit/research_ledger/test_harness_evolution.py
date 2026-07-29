from dataclasses import replace

from qwenpaw.research_ledger.contracts import (
    ResearchArtifactContract,
    ResearchArtifactType,
)
from qwenpaw.research_ledger.episode_package import (
    EpisodeCommand,
    EpisodeExpectedArtifact,
    EpisodePackage,
)
from qwenpaw.research_ledger.harness_evolution import (
    HarnessChangeProposal,
    HarnessEvolutionGate,
    HarnessGateDisposition,
    HarnessMetric,
)


def _episode(path="src/qwenpaw/research_ledger/repair_loop.py"):
    return EpisodePackage(
        episode_id="ep-harness",
        run_id="run-harness",
        task_type="performance",
        repository="owner/repo",
        goal="improve repair loop convergence",
        base_revision="abc123",
        acceptance_criteria=("repair loop converges without regressions",),
        modifiable_files=(path, "tests/unit/research_ledger/test_repair_loop.py"),
        commands=(
            EpisodeCommand(
                "repair-tests",
                "unit",
                ("pytest", "-q", "tests/unit/research_ledger/test_repair_loop.py"),
            ),
        ),
        expected_artifacts=(
            EpisodeExpectedArtifact(
                ResearchArtifactType.CODE_DIFF,
                "implement",
            ),
            EpisodeExpectedArtifact(
                ResearchArtifactType.TEST_RESULT,
                "test",
            ),
            EpisodeExpectedArtifact(
                ResearchArtifactType.REPORT,
                "review",
            ),
        ),
    )


def _artifact(artifact_type, artifact_id):
    return ResearchArtifactContract(
        artifact_id=artifact_id,
        run_id="run-harness",
        step_id="step",
        artifact_type=artifact_type,
        path=f"{artifact_id}.txt",
        content_hash="a" * 64,
        verified=True,
    )


def _proposal(episode):
    return HarnessChangeProposal(
        proposal_id="proposal-1",
        episode_digest=episode.digest(),
        proposer_agent="implementer",
        reviewer_agent="reviewer",
        rollback_revision="abc123",
        changed_paths=("src/qwenpaw/research_ledger/repair_loop.py",),
        artifacts=(
            _artifact(ResearchArtifactType.CODE_DIFF, "diff"),
            _artifact(ResearchArtifactType.TEST_RESULT, "tests"),
            _artifact(ResearchArtifactType.REPORT, "report"),
        ),
        metrics=(
            HarnessMetric(
                name="repair_attempts",
                baseline=3,
                candidate=2,
                lower_is_better=True,
            ),
        ),
    )


def test_harness_gate_approves_independently_reviewed_non_regression():
    episode = _episode()

    result = HarnessEvolutionGate().evaluate(episode, _proposal(episode))

    assert result.approved
    assert result.disposition == HarnessGateDisposition.APPROVED


def test_harness_gate_blocks_scope_violation_and_self_review():
    episode = _episode()
    proposal = replace(
        _proposal(episode),
        reviewer_agent="implementer",
        changed_paths=("src/qwenpaw/app/routers/research.py",),
    )

    result = HarnessEvolutionGate().evaluate(episode, proposal)

    assert result.disposition == HarnessGateDisposition.BLOCKED
    assert "independent_reviewer_required" in result.reasons
    assert any(reason.startswith("scope_violation:") for reason in result.reasons)


def test_harness_gate_blocks_missing_verified_evidence_and_regression():
    episode = _episode()
    proposal = replace(
        _proposal(episode),
        artifacts=(
            replace(
                _artifact(ResearchArtifactType.CODE_DIFF, "diff"),
                verified=False,
            ),
        ),
        metrics=(
            HarnessMetric(
                name="repair_attempts",
                baseline=2,
                candidate=4,
                lower_is_better=True,
            ),
        ),
    )

    result = HarnessEvolutionGate().evaluate(episode, proposal)

    assert result.disposition == HarnessGateDisposition.BLOCKED
    assert "verified_artifact_missing:code_diff" in result.reasons
    assert "verified_artifact_missing:test_result" in result.reasons
    assert "verified_artifact_missing:report" in result.reasons
    assert "metric_regression:repair_attempts" in result.reasons


def test_harness_gate_routes_protected_paths_to_human_review():
    protected = "src/qwenpaw/research_ledger/harness_evolution.py"
    episode = _episode(path=protected)
    proposal = replace(
        _proposal(episode),
        changed_paths=(protected,),
    )

    result = HarnessEvolutionGate().evaluate(episode, proposal)

    assert result.disposition == HarnessGateDisposition.HUMAN_REVIEW
    assert result.reasons[0].startswith("protected_harness_paths:")


def test_harness_gate_blocks_episode_digest_mismatch():
    episode = _episode()
    proposal = replace(_proposal(episode), episode_digest="0" * 64)

    result = HarnessEvolutionGate().evaluate(episode, proposal)

    assert result.disposition == HarnessGateDisposition.BLOCKED
    assert "episode_digest_mismatch" in result.reasons
