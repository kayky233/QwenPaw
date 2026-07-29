"""Two-stage, evidence-gated delivery for completed Issue Campaigns."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from .campaign_delivery_contract import (
    CampaignDeliveryReceipt,
    CampaignPublication,
    CampaignPublisher,
)
from .change_request_delivery import (
    ChangeRequest,
    ChangeRequestProvider,
    EvidenceGatedDelivery,
)
from .contracts import ResearchArtifactContract, ResearchArtifactType
from .episode_package import EpisodePackage
from .step_repository import ResearchArtifactRepository

if TYPE_CHECKING:
    from .issue_campaign import CampaignCandidate


class CampaignChangeRequestDeliverer:
    """Commit and push a candidate, then create a draft PR or MR.

    The publisher is the only component allowed to mint a verified COMMIT
    artifact. The change-request provider is invoked only after that artifact
    passes the run-level evidence gate.
    """

    def __init__(
        self,
        provider: ChangeRequestProvider,
        publisher: CampaignPublisher,
        *,
        base_branch: str,
        draft: bool = True,
    ) -> None:
        if not base_branch.strip():
            raise ValueError("base_branch is required")
        self.provider = provider
        self.publisher = publisher
        self.base_branch = base_branch
        self.draft = draft

    async def deliver(
        self,
        episode: EpisodePackage,
        candidate: CampaignCandidate,
        artifacts: tuple[ResearchArtifactContract, ...],
    ) -> CampaignDeliveryReceipt:
        publication = await self.publisher.publish(
            episode,
            candidate.checkpoint,
        )
        publication.validate(episode.run_id)

        repository = ResearchArtifactRepository()
        for artifact in (*artifacts, publication.artifact):
            repository.add(artifact)
        delivery = EvidenceGatedDelivery(repository, self.provider)
        request = ChangeRequest(
            repository=episode.repository,
            base_branch=self.base_branch,
            head_branch=publication.head_branch,
            title=self._title(episode),
            body=self._body(
                episode,
                candidate,
                (*artifacts, publication.artifact),
                publication,
            ),
            draft=self.draft,
        )
        result = await asyncio.to_thread(
            delivery.create_for_run,
            episode.run_id,
            request,
        )
        request_artifact = self._change_request_artifact(
            episode,
            result.url,
            result.number,
            publication,
        )
        receipt = CampaignDeliveryReceipt(
            change_request=result,
            publication=publication,
            artifacts=(publication.artifact, request_artifact),
        )
        receipt.validate(episode.run_id)
        return receipt

    @staticmethod
    def _title(episode: EpisodePackage) -> str:
        prefix = {
            "bug_fix": "fix",
            "feature": "feat",
            "refactor": "refactor",
            "performance": "perf",
            "research": "research",
        }.get(episode.task_type, "change")
        issue = f" issue #{episode.issue_number}" if episode.issue_number else ""
        return f"{prefix}: {episode.goal}{issue}"[:240]

    @staticmethod
    def _body(
        episode: EpisodePackage,
        candidate: CampaignCandidate,
        artifacts: tuple[ResearchArtifactContract, ...],
        publication: CampaignPublication,
    ) -> str:
        criteria = "\n".join(
            f"- [x] {criterion}"
            for criterion in episode.acceptance_criteria
        )
        evidence = "\n".join(
            "| {type} | {step} | {verified} | `{digest}` |".format(
                type=artifact.artifact_type.value,
                step=artifact.step_id,
                verified="yes" if artifact.verified else "no",
                digest=artifact.content_hash[:12],
            )
            for artifact in sorted(
                artifacts,
                key=lambda item: (
                    item.artifact_type.value,
                    item.step_id,
                    item.artifact_id,
                ),
            )
        )
        changed_paths = sorted(
            {
                str(path)
                for artifact in artifacts
                for path in (
                    artifact.metadata.get("changed_paths", ())
                    if isinstance(
                        artifact.metadata.get("changed_paths", ()),
                        (list, tuple),
                    )
                    else ()
                )
            }
        )
        paths = "\n".join(f"- `{path}`" for path in changed_paths) or "- none"
        issue_line = (
            f"Closes #{episode.issue_number}"
            if episode.issue_number is not None
            else "No linked issue."
        )
        return f"""## AutoResearch delivery

{issue_line}

### Episode

- Episode: `{episode.episode_id}`
- Episode digest: `{episode.digest()}`
- Base revision: `{episode.base_revision}`
- Candidate: `{candidate.checkpoint.candidate_id}`
- Candidate tree: `{candidate.checkpoint.tree_revision}`
- Published commit: `{publication.commit_sha}`
- Head branch: `{publication.head_branch}`
- Risk score: `{candidate.checkpoint.risk_score:.3f}`

### Acceptance criteria

{criteria}

### Changed paths

{paths}

### Verified evidence

| Artifact | Step | Verified | SHA-256 |
|---|---|---:|---|
{evidence}

> Generated by AutoResearch. Automatic merge remains disabled.
"""

    @staticmethod
    def _change_request_artifact(
        episode: EpisodePackage,
        url: str,
        number: int | None,
        publication: CampaignPublication,
    ) -> ResearchArtifactContract:
        return ResearchArtifactContract(
            artifact_id=f"{episode.episode_id}-pull-request",
            run_id=episode.run_id,
            step_id=f"{episode.run_id}-delivery",
            artifact_type=ResearchArtifactType.PULL_REQUEST,
            path=url,
            content_hash=ResearchArtifactContract.hash_content(url),
            verified=bool(url),
            metadata={
                "number": number,
                "commit_sha": publication.commit_sha,
                "head_branch": publication.head_branch,
            },
        )
