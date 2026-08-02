"""Independent Campaign review over host-verified diff and test evidence."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .agent_management_transport import AgentManagementTaskTransport
from .campaign_execution_runner import redact_campaign_output
from .collaboration_contracts import (
    ResearchAgentRole,
    ReviewDecision,
    ReviewVerdict,
    TaskEnvelope,
)
from .contracts import ResearchArtifactContract, ResearchArtifactType
from .episode_package import EpisodePackage
from .issue_campaign import CampaignCandidate, CampaignReview

_MAX_DIFF_CHARACTERS = 200_000
_MAX_OUTPUT_CHARACTERS = 50_000


def _truncate(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[:limit] + f"\n...[truncated {len(value) - limit} characters]"


def _verified_diff(
    episode: EpisodePackage,
    artifacts: tuple[ResearchArtifactContract, ...],
) -> str:
    candidates = tuple(
        artifact
        for artifact in artifacts
        if artifact.artifact_type == ResearchArtifactType.CODE_DIFF
        and artifact.run_id == episode.run_id
        and artifact.verified
    )
    if not candidates:
        raise RuntimeError("Reviewer requires a verified CODE_DIFF artifact")
    artifact = candidates[-1]
    path = Path(artifact.path).expanduser().resolve()
    if not path.is_file():
        raise RuntimeError(f"verified Campaign patch is missing: {path}")
    content = path.read_text(encoding="utf-8")
    digest = ResearchArtifactContract.hash_content(content)
    if digest != artifact.content_hash:
        raise RuntimeError("verified Campaign patch hash mismatch")
    if len(content) > _MAX_DIFF_CHARACTERS:
        raise RuntimeError(
            "verified Campaign diff exceeds the independent review limit; "
            "split the change into smaller Campaigns instead of reviewing a "
            f"truncated diff ({len(content)} > {_MAX_DIFF_CHARACTERS})"
        )
    return content


def _verified_tests(
    episode: EpisodePackage,
    artifacts: tuple[ResearchArtifactContract, ...],
) -> tuple[dict[str, Any], ...]:
    results: list[dict[str, Any]] = []
    for artifact in artifacts:
        if artifact.artifact_type != ResearchArtifactType.TEST_RESULT:
            continue
        if artifact.run_id != episode.run_id or not artifact.verified:
            continue
        payload = dict(artifact.metadata)
        encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False)
        if ResearchArtifactContract.hash_content(encoded) != artifact.content_hash:
            raise RuntimeError(
                f"test evidence hash mismatch: {artifact.artifact_id}"
            )
        results.append(
            {
                "artifact_id": artifact.artifact_id,
                "argv": list(payload.get("argv", ())),
                "cwd": str(payload.get("cwd", "")),
                "exit_code": payload.get("exit_code"),
                "timed_out": bool(payload.get("timed_out", False)),
                "duration_seconds": payload.get("duration_seconds"),
                "stdout": _truncate(
                    redact_campaign_output(str(payload.get("stdout", ""))),
                    _MAX_OUTPUT_CHARACTERS,
                ),
                "stderr": _truncate(
                    redact_campaign_output(str(payload.get("stderr", ""))),
                    _MAX_OUTPUT_CHARACTERS,
                ),
                "content_hash": artifact.content_hash,
            }
        )
    if not results:
        raise RuntimeError("Reviewer requires verified TEST_RESULT evidence")
    return tuple(results)


def _parse_review_payload(raw: str) -> dict[str, Any]:
    text = raw.strip()
    if text.startswith("```") and text.endswith("```"):
        lines = text.splitlines()
        text = "\n".join(lines[1:-1]).strip()
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise RuntimeError("Reviewer did not return one valid JSON object") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("Reviewer response must be a JSON object")
    allowed_keys = {"verdict", "findings", "required_changes"}
    unknown = set(payload) - allowed_keys
    if unknown:
        raise RuntimeError(
            "Reviewer response contains unsupported keys: "
            + ", ".join(sorted(unknown))
        )
    verdict = str(payload.get("verdict", ""))
    if verdict not in {item.value for item in ReviewVerdict}:
        raise RuntimeError(f"Reviewer returned invalid verdict: {verdict!r}")
    for key in ("findings", "required_changes"):
        value = payload.get(key, [])
        if not isinstance(value, list) or not all(
            isinstance(item, str) for item in value
        ):
            raise RuntimeError(f"Reviewer field {key!r} must be an array of strings")
    return payload


class HostEvidenceCampaignReviewer:
    """Send bounded, hash-verified evidence to a separate read-only agent."""

    def __init__(
        self,
        transport: AgentManagementTaskTransport,
        agent_id: str,
        *,
        from_agent: str,
        root_session_id: str | None,
        emit: Any,
    ) -> None:
        self.transport = transport
        self.agent_id = agent_id
        self.from_agent = from_agent
        self.root_session_id = root_session_id
        self.emit = emit

    async def review(
        self,
        episode: EpisodePackage,
        candidate: CampaignCandidate,
        artifacts: tuple[ResearchArtifactContract, ...],
    ) -> CampaignReview:
        diff = _verified_diff(episode, artifacts)
        tests = _verified_tests(episode, artifacts)
        self.emit("reviewing", f"Reviewer agent {self.agent_id} started")
        envelope = TaskEnvelope(
            task_id=episode.episode_id,
            run_id=episode.run_id,
            step_id=f"{episode.run_id}-review",
            role=ResearchAgentRole.REVIEWER,
            objective=(
                "Independently review the host-verified candidate below. "
                "Do not modify files or request tools. Check correctness, "
                "scope, regression risk, security, and whether the tests support "
                "the acceptance criteria. Return exactly one JSON object with "
                "keys verdict (approve, request_changes, or blocked), findings "
                "(array of strings), and required_changes (array of strings).\n\n"
                f"Goal: {episode.goal}\n"
                f"Acceptance criteria: {list(episode.acceptance_criteria)}\n"
                f"Approved paths: {list(episode.modifiable_files)}\n"
                f"Candidate tree: {candidate.checkpoint.tree_revision}\n"
                f"Candidate diff SHA-256: {candidate.checkpoint.diff_hash}\n\n"
                "--- HOST-VERIFIED COMPLETE DIFF ---\n"
                f"{diff}\n"
                "--- END DIFF ---\n\n"
                "--- HOST-VERIFIED TEST EVIDENCE ---\n"
                f"{json.dumps(tests, ensure_ascii=False, indent=2)}\n"
                "--- END TEST EVIDENCE ---"
            ),
            expected_artifact_types=(ResearchArtifactType.REPORT,),
            allowed_paths=(),
            read_only=True,
        )
        result = await self.transport.send(
            self.agent_id,
            envelope,
            from_agent=self.from_agent,
            root_session_id=self.root_session_id,
        )
        payload = _parse_review_payload(result.text)
        decision = ReviewDecision(
            run_id=episode.run_id,
            step_id=envelope.step_id,
            verdict=ReviewVerdict(str(payload["verdict"])),
            findings=tuple(str(item) for item in payload.get("findings", ())),
            required_changes=tuple(
                str(item) for item in payload.get("required_changes", ())
            ),
        )
        report = ResearchArtifactContract(
            artifact_id=f"{candidate.checkpoint.candidate_id}-review",
            run_id=episode.run_id,
            step_id=envelope.step_id,
            artifact_type=ResearchArtifactType.REPORT,
            path=f"agent://{self.agent_id}/{result.session_id}",
            content_hash=ResearchArtifactContract.hash_content(result.text),
            verified=True,
            metadata={
                "agent_id": self.agent_id,
                "session_id": result.session_id,
                "verdict": decision.verdict.value,
                "findings": list(decision.findings),
                "required_changes": list(decision.required_changes),
                "candidate_diff_hash": candidate.checkpoint.diff_hash,
                "test_artifact_count": len(tests),
                "evidence_source": "host_verified_complete",
                "diff_characters": len(diff),
            },
        )
        self.emit("reviewed", f"Reviewer verdict: {decision.verdict.value}")
        return CampaignReview(decision, report)
