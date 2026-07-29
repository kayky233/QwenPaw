from pathlib import Path

import pytest

from qwenpaw.research_ledger.contracts import (
    ResearchArtifactContract,
    ResearchArtifactType,
)
from qwenpaw.research_ledger.episode_evidence_gate import EpisodeEvidenceGate
from qwenpaw.research_ledger.episode_package import (
    EpisodeCommand,
    EpisodeExpectedArtifact,
    EpisodePackage,
)
from qwenpaw.research_ledger.episode_validation import EpisodeValidationPlanner
from qwenpaw.research_ledger.validation_contract import ValidationStage


def _episode(*, commands=None):
    return EpisodePackage(
        episode_id="ep-1",
        run_id="run-1",
        task_type="bug_fix",
        repository="owner/repo",
        goal="fix cache expiry",
        base_revision="base",
        acceptance_criteria=("expired entries are hidden",),
        modifiable_files=(
            "src/qwenpaw/memory/cache.py",
            "tests/unit/test_cache.py",
        ),
        commands=commands
        or (
            EpisodeCommand(
                "focused",
                "unit",
                ("pytest", "-q", "tests/unit/test_cache.py"),
                cwd=".",
            ),
            EpisodeCommand(
                "lint",
                "lint",
                ("ruff", "check", "src/qwenpaw/memory/cache.py"),
                cwd=".",
            ),
            EpisodeCommand(
                "browser",
                "e2e",
                ("pytest", "-q", "tests/e2e/test_cache.py"),
                cwd=".",
                required=False,
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
            EpisodeExpectedArtifact(
                ResearchArtifactType.COMMIT,
                "delivery",
            ),
        ),
    )


def _artifact(
    artifact_type,
    step_id,
    *,
    run_id="run-1",
    verified=True,
    metadata=None,
):
    return ResearchArtifactContract(
        artifact_id=f"{step_id}-{artifact_type.value}",
        run_id=run_id,
        step_id=step_id,
        artifact_type=artifact_type,
        path=f"{artifact_type.value}.json",
        content_hash="a" * 64,
        verified=verified,
        metadata=metadata or {},
    )


def _complete_artifacts():
    return (
        _artifact(
            ResearchArtifactType.CODE_DIFF,
            "run-1-implement",
            metadata={
                "changed_paths": [
                    "src/qwenpaw/memory/cache.py",
                    "tests/unit/test_cache.py",
                ]
            },
        ),
        _artifact(ResearchArtifactType.TEST_RESULT, "run-1-test"),
        _artifact(ResearchArtifactType.REPORT, "run-1-review"),
        _artifact(ResearchArtifactType.COMMIT, "run-1-delivery"),
    )


def test_episode_validation_planner_maps_stages_and_workspace(tmp_path):
    plan = EpisodeValidationPlanner().compile(
        _episode(),
        workspace=str(tmp_path),
    )

    assert [item.stage for item in plan.commands] == [
        ValidationStage.FOCUSED,
        ValidationStage.QUALITY,
        ValidationStage.E2E,
    ]
    assert all(Path(item.cwd) == tmp_path.resolve() for item in plan.commands)
    assert plan.commands[-1].required is False


def test_episode_validation_planner_rejects_unknown_stage(tmp_path):
    episode = _episode(
        commands=(EpisodeCommand("custom", "unknown", ("tool",)),)
    )

    with pytest.raises(ValueError, match="unsupported episode command stage"):
        EpisodeValidationPlanner().compile(
            episode,
            workspace=str(tmp_path),
        )


def test_episode_evidence_gate_accepts_complete_verified_run_evidence():
    result = EpisodeEvidenceGate().evaluate(
        _episode(),
        _complete_artifacts(),
    )

    assert result.ready
    assert result.missing_artifacts == ()
    assert result.invalid_artifacts == ()
    assert result.scope_violations == ()


def test_episode_evidence_gate_rejects_missing_and_unverified_artifacts():
    artifacts = tuple(
        artifact
        for artifact in _complete_artifacts()
        if artifact.artifact_type != ResearchArtifactType.REPORT
    )
    artifacts = tuple(
        artifact
        if artifact.artifact_type != ResearchArtifactType.TEST_RESULT
        else ResearchArtifactContract(
            artifact_id=artifact.artifact_id,
            run_id=artifact.run_id,
            step_id=artifact.step_id,
            artifact_type=artifact.artifact_type,
            path=artifact.path,
            content_hash=artifact.content_hash,
            verified=False,
            metadata=artifact.metadata,
        )
        for artifact in artifacts
    )

    result = EpisodeEvidenceGate().evaluate(_episode(), artifacts)

    assert result.ready is False
    assert result.missing_artifacts == ("report", "test_result")


def test_episode_evidence_gate_rejects_cross_run_evidence():
    artifacts = (
        _artifact(
            ResearchArtifactType.CODE_DIFF,
            "run-2-implement",
            run_id="run-2",
            metadata={
                "changed_paths": ["src/qwenpaw/memory/cache.py"]
            },
        ),
        *_complete_artifacts()[1:],
    )

    result = EpisodeEvidenceGate().evaluate(_episode(), artifacts)

    assert result.ready is False
    assert "code_diff" in result.missing_artifacts
    assert result.invalid_artifacts == (
        "run-2-implement-code_diff:run_id_mismatch",
    )


def test_episode_evidence_gate_rejects_diff_without_changed_paths():
    artifacts = (
        _artifact(ResearchArtifactType.CODE_DIFF, "run-1-implement"),
        *_complete_artifacts()[1:],
    )

    result = EpisodeEvidenceGate().evaluate(_episode(), artifacts)

    assert result.ready is False
    assert result.invalid_artifacts == (
        "run-1-implement-code_diff:changed_paths_missing",
    )


def test_episode_evidence_gate_rejects_out_of_scope_path():
    artifacts = (
        _artifact(
            ResearchArtifactType.CODE_DIFF,
            "run-1-implement",
            metadata={
                "changed_paths": [
                    "src/qwenpaw/app/routers/research.py"
                ]
            },
        ),
        *_complete_artifacts()[1:],
    )

    result = EpisodeEvidenceGate().evaluate(_episode(), artifacts)

    assert result.ready is False
    assert result.scope_violations == (
        "src/qwenpaw/app/routers/research.py",
    )
