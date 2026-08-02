from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from qwenpaw.research_ledger.candidate_checkpoint import CandidateCheckpoint
from qwenpaw.research_ledger.contracts import (
    ResearchArtifactContract,
    ResearchArtifactType,
)
from qwenpaw.research_ledger.episode_package import (
    EpisodeCommand,
    EpisodeExpectedArtifact,
    EpisodePackage,
)
from qwenpaw.research_ledger.host_evidence_reviewer import (
    HostEvidenceCampaignReviewer,
    _MAX_DIFF_CHARACTERS,
)
from qwenpaw.research_ledger.issue_campaign import CampaignCandidate


class _Transport:
    def __init__(self) -> None:
        self.calls = []

    async def send(self, agent_id, envelope, **kwargs):
        self.calls.append((agent_id, envelope, kwargs))
        return SimpleNamespace(
            text='{"verdict":"approve","findings":[],"required_changes":[]}',
            session_id="review-session",
        )


def _episode() -> EpisodePackage:
    return EpisodePackage(
        episode_id="episode-security",
        run_id="run-security",
        task_type="bug_fix",
        repository="owner/repository",
        issue_number=8,
        goal="Review the complete verified patch",
        base_revision="a" * 40,
        acceptance_criteria=("No credential reaches the reviewer",),
        modifiable_files=("src/fix.py",),
        commands=(
            EpisodeCommand(
                command_id="unit",
                stage="unit",
                argv=("pytest", "-q"),
            ),
        ),
        expected_artifacts=(
            EpisodeExpectedArtifact(ResearchArtifactType.CODE_DIFF, "implement"),
            EpisodeExpectedArtifact(ResearchArtifactType.TEST_RESULT, "test"),
            EpisodeExpectedArtifact(ResearchArtifactType.REPORT, "review"),
        ),
    )


def _candidate_and_artifacts(tmp_path: Path, diff: str):
    patch = tmp_path / "candidate.patch"
    patch.write_text(diff, encoding="utf-8")
    diff_hash = ResearchArtifactContract.hash_content(diff)
    diff_artifact = ResearchArtifactContract(
        artifact_id="security-diff",
        run_id="run-security",
        step_id="run-security-implement",
        artifact_type=ResearchArtifactType.CODE_DIFF,
        path=str(patch),
        content_hash=diff_hash,
        verified=True,
        metadata={"changed_paths": ["src/fix.py"]},
    )
    metadata = {
        "argv": ["pytest", "-q"],
        "cwd": str(tmp_path),
        "exit_code": 0,
        "stdout": "token=github_pat_abcdefghijklmnopqrstuvwxyz123456",
        "stderr": "Authorization: Bearer ghp_abcdefghijklmnopqrstuvwxyz123456",
        "duration_seconds": 0.1,
        "timed_out": False,
    }
    encoded = json.dumps(metadata, sort_keys=True, ensure_ascii=False)
    test_artifact = ResearchArtifactContract(
        artifact_id="security-test",
        run_id="run-security",
        step_id="run-security-test",
        artifact_type=ResearchArtifactType.TEST_RESULT,
        path="security-test.json",
        content_hash=ResearchArtifactContract.hash_content(encoded),
        verified=True,
        metadata=metadata,
    )
    candidate = CampaignCandidate(
        CandidateCheckpoint(
            candidate_id="security-candidate",
            run_id="run-security",
            parent_revision="a" * 40,
            tree_revision="b" * 40,
            diff_hash=diff_hash,
            verification_passed=True,
        ),
        (diff_artifact,),
    )
    return candidate, (diff_artifact, test_artifact)


@pytest.mark.asyncio
async def test_reviewer_blocks_diff_that_cannot_be_reviewed_completely(
    tmp_path: Path,
) -> None:
    candidate, artifacts = _candidate_and_artifacts(
        tmp_path,
        "x" * (_MAX_DIFF_CHARACTERS + 1),
    )
    transport = _Transport()
    reviewer = HostEvidenceCampaignReviewer(
        transport,
        "reviewer",
        from_agent="default",
        root_session_id=None,
        emit=lambda *_: None,
    )

    with pytest.raises(RuntimeError, match="exceeds the independent review limit"):
        await reviewer.review(_episode(), candidate, artifacts)

    assert transport.calls == []


@pytest.mark.asyncio
async def test_reviewer_redacts_test_credentials_before_agent_transport(
    tmp_path: Path,
) -> None:
    candidate, artifacts = _candidate_and_artifacts(
        tmp_path,
        "diff --git a/src/fix.py b/src/fix.py\n+VALUE = 2\n",
    )
    transport = _Transport()
    reviewer = HostEvidenceCampaignReviewer(
        transport,
        "reviewer",
        from_agent="default",
        root_session_id=None,
        emit=lambda *_: None,
    )

    result = await reviewer.review(_episode(), candidate, artifacts)

    objective = transport.calls[0][1].objective
    assert "github_pat_" not in objective
    assert "ghp_" not in objective
    assert "[REDACTED]" in objective
    assert result.report.metadata["evidence_source"] == "host_verified_complete"
    assert result.report.metadata["diff_characters"] > 0
