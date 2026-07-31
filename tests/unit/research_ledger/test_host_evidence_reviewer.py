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
)
from qwenpaw.research_ledger.issue_campaign import CampaignCandidate


class _Transport:
    def __init__(self, text: str) -> None:
        self.text = text
        self.calls = []

    async def send(self, agent_id, envelope, **kwargs):
        self.calls.append((agent_id, envelope, kwargs))
        return SimpleNamespace(text=self.text, session_id="review-session")


def _episode() -> EpisodePackage:
    return EpisodePackage(
        episode_id="episode-1",
        run_id="run-1",
        task_type="bug_fix",
        repository="owner/repository",
        issue_number=7,
        goal="Fix the verified regression",
        base_revision="a" * 40,
        acceptance_criteria=("The regression is fixed",),
        modifiable_files=("src/fix.py", "tests/test_fix.py"),
        commands=(
            EpisodeCommand(
                command_id="unit",
                stage="unit",
                argv=("pytest", "-q", "tests/test_fix.py"),
            ),
        ),
        expected_artifacts=(
            EpisodeExpectedArtifact(ResearchArtifactType.CODE_DIFF, "implement"),
            EpisodeExpectedArtifact(ResearchArtifactType.TEST_RESULT, "test"),
            EpisodeExpectedArtifact(ResearchArtifactType.REPORT, "review"),
        ),
    )


def _evidence(tmp_path: Path):
    diff = "diff --git a/src/fix.py b/src/fix.py\n+VALUE = 2\n"
    patch = tmp_path / "candidate.patch"
    patch.write_text(diff, encoding="utf-8")
    diff_artifact = ResearchArtifactContract(
        artifact_id="candidate-diff",
        run_id="run-1",
        step_id="run-1-implement",
        artifact_type=ResearchArtifactType.CODE_DIFF,
        path=str(patch),
        content_hash=ResearchArtifactContract.hash_content(diff),
        verified=True,
        metadata={"changed_paths": ["src/fix.py"]},
    )
    test_metadata = {
        "argv": ["pytest", "-q", "tests/test_fix.py"],
        "cwd": str(tmp_path),
        "exit_code": 0,
        "stdout": "1 passed",
        "stderr": "",
        "duration_seconds": 0.1,
        "timed_out": False,
        "runner_name": "LocalSubprocessRunner",
    }
    test_content = json.dumps(
        test_metadata,
        sort_keys=True,
        ensure_ascii=False,
    )
    test_artifact = ResearchArtifactContract(
        artifact_id="run-1-test-unit-0",
        run_id="run-1",
        step_id="run-1-test",
        artifact_type=ResearchArtifactType.TEST_RESULT,
        path="run-1-test-unit-0.json",
        content_hash=ResearchArtifactContract.hash_content(test_content),
        verified=True,
        metadata=test_metadata,
    )
    checkpoint = CandidateCheckpoint(
        candidate_id="candidate-1",
        run_id="run-1",
        parent_revision="a" * 40,
        tree_revision="b" * 40,
        diff_hash=diff_artifact.content_hash,
        verification_passed=True,
    )
    return CampaignCandidate(checkpoint, (diff_artifact,)), (
        diff_artifact,
        test_artifact,
    )


@pytest.mark.asyncio
async def test_reviewer_receives_real_host_verified_diff_and_tests(
    tmp_path: Path,
) -> None:
    candidate, artifacts = _evidence(tmp_path)
    transport = _Transport(
        json.dumps(
            {
                "verdict": "approve",
                "findings": ["The focused regression is covered."],
                "required_changes": [],
            }
        )
    )
    events = []
    reviewer = HostEvidenceCampaignReviewer(
        transport,
        "reviewer",
        from_agent="default",
        root_session_id="session-1",
        emit=lambda phase, detail: events.append((phase, detail)),
    )

    result = await reviewer.review(_episode(), candidate, artifacts)

    _, envelope, kwargs = transport.calls[0]
    assert envelope.read_only is True
    assert envelope.allowed_paths == ()
    assert "+VALUE = 2" in envelope.objective
    assert "pytest" in envelope.objective
    assert "1 passed" in envelope.objective
    assert kwargs["from_agent"] == "default"
    assert result.report.verified is True
    assert result.report.metadata["evidence_source"] == "host_verified"
    assert result.report.metadata["candidate_diff_hash"] == (
        candidate.checkpoint.diff_hash
    )
    assert [phase for phase, _ in events] == ["reviewing", "reviewed"]


@pytest.mark.asyncio
async def test_reviewer_blocks_tampered_patch_before_transport(
    tmp_path: Path,
) -> None:
    candidate, artifacts = _evidence(tmp_path)
    Path(artifacts[0].path).write_text("tampered", encoding="utf-8")
    transport = _Transport(
        '{"verdict":"approve","findings":[],"required_changes":[]}'
    )
    reviewer = HostEvidenceCampaignReviewer(
        transport,
        "reviewer",
        from_agent="default",
        root_session_id=None,
        emit=lambda *_: None,
    )

    with pytest.raises(RuntimeError, match="patch hash mismatch"):
        await reviewer.review(_episode(), candidate, artifacts)

    assert transport.calls == []


@pytest.mark.asyncio
async def test_reviewer_blocks_tampered_test_evidence_before_transport(
    tmp_path: Path,
) -> None:
    candidate, artifacts = _evidence(tmp_path)
    artifacts[1].metadata["stdout"] = "tampered output"
    transport = _Transport(
        '{"verdict":"approve","findings":[],"required_changes":[]}'
    )
    reviewer = HostEvidenceCampaignReviewer(
        transport,
        "reviewer",
        from_agent="default",
        root_session_id=None,
        emit=lambda *_: None,
    )

    with pytest.raises(RuntimeError, match="test evidence hash mismatch"):
        await reviewer.review(_episode(), candidate, artifacts)

    assert transport.calls == []


@pytest.mark.asyncio
async def test_reviewer_rejects_non_json_decision(tmp_path: Path) -> None:
    candidate, artifacts = _evidence(tmp_path)
    transport = _Transport("Looks good to me")
    reviewer = HostEvidenceCampaignReviewer(
        transport,
        "reviewer",
        from_agent="default",
        root_session_id=None,
        emit=lambda *_: None,
    )

    with pytest.raises(RuntimeError, match="valid JSON object"):
        await reviewer.review(_episode(), candidate, artifacts)
