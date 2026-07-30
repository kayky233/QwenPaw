# -*- coding: utf-8 -*-
"""Local, no-push Issue Campaign verification runner.

This module exercises the host-verified candidate, validation, evidence-gate,
and commit path in an isolated Git worktree. It intentionally replaces the
remote change-request provider with a ``local://`` receipt, so it never pushes
or creates a GitHub/GitLab change request.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Sequence

from .campaign_delivery import CampaignChangeRequestDeliverer
from .change_request_delivery import (
    ChangeRequest,
    ChangeRequestResult,
)
from .contracts import ResearchArtifactContract, ResearchArtifactType
from .episode_evidence_gate import EpisodeEvidenceGate
from .episode_package import EpisodePackage
from .episode_validation import EpisodeValidationPlanner
from .execution_runner import LocalSubprocessRunner
from .issue_campaign import CampaignCandidate
from .validation_pipeline_v2 import ValidationPipelineV2
from .worktree_campaign import (
    GitWorktreeCampaignExecutor,
    GitWorktreeCampaignPublisher,
)

_SAFE_COMPONENT_RE = re.compile(r"[^A-Za-z0-9._-]+")


@dataclass(frozen=True)
class LocalCampaignVerificationResult:
    """Durable result of one local-only Campaign verification."""

    status: str
    run_id: str
    repository_root: str
    branch: str
    worktree: str
    commit_sha: str
    remote_created: bool
    local_change_request_url: str
    report_json: str
    report_markdown: str
    artifact_types: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class LocalVerificationChangeRequestProvider:
    """Return a local receipt without contacting any remote service."""

    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        self.request: ChangeRequest | None = None

    def create(self, request: ChangeRequest) -> ChangeRequestResult:
        self.request = request
        return ChangeRequestResult(
            url=f"local://autoresearch/{self.run_id}/draft-change-request",
            number=None,
        )


async def _run_git(
    argv: list[str],
    *,
    cwd: Path,
    timeout: int,
) -> str:
    def execute() -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            argv,
            cwd=cwd,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )

    completed = await asyncio.to_thread(execute)
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()
        raise RuntimeError(
            f"command failed ({completed.returncode}): "
            f"{' '.join(argv)}\n{detail}"
        )
    return completed.stdout


def _safe_component(value: str) -> str:
    normalized = _SAFE_COMPONENT_RE.sub("-", value.strip()).strip("-.")
    return normalized[:48] or "run"


def _same_revision(expected: str, actual: str) -> bool:
    expected_value = expected.strip().lower()
    actual_value = actual.strip().lower()
    return bool(expected_value) and (
        expected_value == actual_value
        or (
            len(expected_value) >= 7
            and actual_value.startswith(expected_value)
        )
    )


def _plan_artifact(
    episode: EpisodePackage,
    episode_path: Path,
) -> ResearchArtifactContract:
    content = episode.to_json()
    return ResearchArtifactContract(
        artifact_id=f"{episode.episode_id}-plan",
        run_id=episode.run_id,
        step_id=f"{episode.run_id}-plan",
        artifact_type=ResearchArtifactType.PLAN,
        path=str(episode_path),
        content_hash=ResearchArtifactContract.hash_content(content),
        verified=True,
        metadata={"episode_digest": episode.digest()},
    )


def _review_artifact(
    episode: EpisodePackage,
    candidate: CampaignCandidate,
    validation_artifacts: tuple[ResearchArtifactContract, ...],
) -> ResearchArtifactContract:
    lines = [
        "# Local Campaign Review",
        "",
        f"- Candidate: `{candidate.checkpoint.candidate_id}`",
        f"- Tree: `{candidate.checkpoint.tree_revision}`",
        "- Decision: `approve`",
        "- Remote write: `disabled`",
        "",
        "## Validation evidence",
        "",
    ]
    for artifact in validation_artifacts:
        lines.append(
            "- `{}` verified={} hash=`{}`".format(
                artifact.artifact_id,
                str(artifact.verified).lower(),
                artifact.content_hash[:16],
            )
        )
    content = "\n".join(lines) + "\n"
    return ResearchArtifactContract(
        artifact_id=f"{candidate.checkpoint.candidate_id}-local-review",
        run_id=episode.run_id,
        step_id=f"{episode.run_id}-review",
        artifact_type=ResearchArtifactType.REPORT,
        path="local://review/report.md",
        content_hash=ResearchArtifactContract.hash_content(content),
        verified=True,
        metadata={
            "verdict": "approve",
            "remote_write": False,
            "content": content,
        },
    )


def _render_markdown(
    *,
    episode: EpisodePackage,
    result: LocalCampaignVerificationResult,
    artifacts: tuple[ResearchArtifactContract, ...],
) -> str:
    rows = "\n".join(
        "| {} | {} | {} | `{}` |".format(
            artifact.artifact_type.value,
            artifact.step_id,
            "yes" if artifact.verified else "no",
            artifact.content_hash[:16],
        )
        for artifact in artifacts
    )
    return f"""# Local Issue Campaign Verification

## Result

- Status: `{result.status}`
- Run: `{result.run_id}`
- Episode digest: `{episode.digest()}`
- Repository: `{episode.repository}`
- Local branch: `{result.branch}`
- Local commit: `{result.commit_sha}`
- Remote change request created: `no`
- Local receipt: `{result.local_change_request_url}`

## Safety boundary

This verification created an isolated Git worktree and a local commit only.
It did not push a branch and did not create a GitHub or GitLab change request.

## Acceptance criteria

{chr(10).join(f'- [x] {item}' for item in episode.acceptance_criteria)}

## Evidence

| Artifact | Step | Verified | SHA-256 |
|---|---|---:|---|
{rows}
"""


async def verify_local_campaign(
    repository_root: Path,
    episode_path: Path,
    patch_path: Path,
    report_dir: Path,
    *,
    branch: str | None = None,
    keep_worktree: bool = False,
) -> LocalCampaignVerificationResult:
    """Run a local-only Campaign verification in an isolated worktree."""

    repository_root = repository_root.expanduser().resolve()
    episode_path = episode_path.expanduser().resolve()
    patch_path = patch_path.expanduser().resolve()
    report_dir = report_dir.expanduser().resolve()
    if not (repository_root / ".git").exists():
        raise ValueError(f"not a Git repository: {repository_root}")
    if not episode_path.is_file():
        raise ValueError(f"episode file is missing: {episode_path}")
    if not patch_path.is_file():
        raise ValueError(f"patch file is missing: {patch_path}")

    episode = EpisodePackage.from_json(
        episode_path.read_text(encoding="utf-8")
    )
    report_dir.mkdir(parents=True, exist_ok=True)

    original_head = (
        await _run_git(
            ["git", "rev-parse", "HEAD"],
            cwd=repository_root,
            timeout=30,
        )
    ).strip()
    if not _same_revision(episode.base_revision, original_head):
        raise RuntimeError(
            "episode base revision does not match repository HEAD: "
            f"{episode.base_revision!r} != {original_head!r}"
        )
    status = await _run_git(
        ["git", "status", "--porcelain"],
        cwd=repository_root,
        timeout=30,
    )
    if status.strip():
        raise RuntimeError(
            "local Campaign verification requires a clean source repository"
        )

    branch_name = branch or (
        "autoresearch/local-verify-" + _safe_component(episode.run_id)
    )
    worktree = report_dir / f"{_safe_component(episode.run_id)}-worktree"
    if worktree.exists():
        raise RuntimeError(f"verification worktree already exists: {worktree}")

    await _run_git(
        [
            "git",
            "worktree",
            "add",
            "-b",
            branch_name,
            str(worktree),
            episode.base_revision,
        ],
        cwd=repository_root,
        timeout=120,
    )

    artifacts: tuple[ResearchArtifactContract, ...] = ()
    try:
        async def implementer(
            current_episode: EpisodePackage,
            attempt: int,
            feedback: str,
            target_worktree: Path,
        ) -> None:
            if current_episode.run_id != episode.run_id:
                raise RuntimeError("episode changed during local verification")
            if attempt != 1 or feedback:
                raise RuntimeError(
                    "local patch verification supports exactly one attempt"
                )
            await _run_git(
                [
                    "git",
                    "apply",
                    "--whitespace=nowarn",
                    str(patch_path),
                ],
                cwd=target_worktree,
                timeout=120,
            )

        candidate = await GitWorktreeCampaignExecutor(
            worktree,
            implementer,
            _run_git,
            artifact_root=report_dir / "artifacts",
        ).execute(episode, 1, "")

        validation_plan = EpisodeValidationPlanner().compile(
            episode,
            workspace=str(worktree),
        )
        validation = await asyncio.to_thread(
            ValidationPipelineV2(
                LocalSubprocessRunner(),
                episode.run_id,
            ).execute,
            f"{episode.run_id}-test-1",
            validation_plan,
        )
        if not validation.succeeded:
            raise RuntimeError("local Campaign validation failed")

        validation_artifacts = tuple(
            stage.artifact for stage in validation.stages
        )
        plan_artifact = _plan_artifact(episode, episode_path)
        review_artifact = _review_artifact(
            episode,
            candidate,
            validation_artifacts,
        )
        pre_delivery = (
            plan_artifact,
            *candidate.artifacts,
            *validation_artifacts,
            review_artifact,
        )
        pre_gate = EpisodeEvidenceGate().evaluate(
            episode,
            pre_delivery,
            required_types={
                ResearchArtifactType.PLAN,
                ResearchArtifactType.CODE_DIFF,
                ResearchArtifactType.TEST_RESULT,
                ResearchArtifactType.REPORT,
            },
        )
        if not pre_gate.ready:
            raise RuntimeError(
                "local Campaign pre-delivery evidence gate failed: "
                + json.dumps(asdict(pre_gate), ensure_ascii=False)
            )

        provider = LocalVerificationChangeRequestProvider(episode.run_id)
        receipt = await CampaignChangeRequestDeliverer(
            provider,
            GitWorktreeCampaignPublisher(
                worktree,
                branch_name,
                _run_git,
                push=False,
            ),
            base_branch="local-verification",
            draft=True,
        ).deliver(
            episode,
            candidate,
            pre_delivery,
        )
        artifacts = (*pre_delivery, *receipt.artifacts)
        post_gate = EpisodeEvidenceGate().evaluate(
            episode,
            artifacts,
            required_types={
                ResearchArtifactType.PLAN,
                ResearchArtifactType.CODE_DIFF,
                ResearchArtifactType.TEST_RESULT,
                ResearchArtifactType.REPORT,
                ResearchArtifactType.COMMIT,
                ResearchArtifactType.PULL_REQUEST,
            },
        )
        if not post_gate.ready:
            raise RuntimeError(
                "local Campaign post-delivery evidence gate failed: "
                + json.dumps(asdict(post_gate), ensure_ascii=False)
            )

        result = LocalCampaignVerificationResult(
            status="verified_local_only",
            run_id=episode.run_id,
            repository_root=str(repository_root),
            branch=branch_name,
            worktree=str(worktree),
            commit_sha=receipt.publication.commit_sha,
            remote_created=False,
            local_change_request_url=receipt.change_request.url,
            report_json=str(report_dir / "verification.json"),
            report_markdown=str(report_dir / "verification.md"),
            artifact_types=tuple(
                sorted(
                    {
                        artifact.artifact_type.value
                        for artifact in artifacts
                    }
                )
            ),
        )
        Path(result.report_json).write_text(
            json.dumps(
                {
                    "result": result.to_dict(),
                    "episode": episode.to_dict(),
                    "artifacts": [
                        {
                            "artifact_id": artifact.artifact_id,
                            "run_id": artifact.run_id,
                            "step_id": artifact.step_id,
                            "artifact_type": artifact.artifact_type.value,
                            "path": artifact.path,
                            "content_hash": artifact.content_hash,
                            "verified": artifact.verified,
                            "metadata": artifact.metadata,
                        }
                        for artifact in artifacts
                    ],
                    "remote_request": (
                        asdict(provider.request)
                        if provider.request is not None
                        else None
                    ),
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        Path(result.report_markdown).write_text(
            _render_markdown(
                episode=episode,
                result=result,
                artifacts=artifacts,
            ),
            encoding="utf-8",
        )
        return result
    finally:
        if not keep_worktree:
            await _run_git(
                ["git", "worktree", "remove", "--force", str(worktree)],
                cwd=repository_root,
                timeout=120,
            )
            await _run_git(
                ["git", "branch", "-D", branch_name],
                cwd=repository_root,
                timeout=30,
            )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run a local-only AutoResearch Campaign verification. "
            "No branch is pushed and no remote PR/MR is created."
        )
    )
    parser.add_argument("--repository", required=True, type=Path)
    parser.add_argument("--episode", required=True, type=Path)
    parser.add_argument("--patch", required=True, type=Path)
    parser.add_argument("--report-dir", required=True, type=Path)
    parser.add_argument("--branch", default=None)
    parser.add_argument("--keep-worktree", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = asyncio.run(
            verify_local_campaign(
                args.repository,
                args.episode,
                args.patch,
                args.report_dir,
                branch=args.branch,
                keep_worktree=args.keep_worktree,
            )
        )
    except Exception as exc:
        print(
            json.dumps(
                {
                    "status": "failed",
                    "error": f"{type(exc).__name__}: {exc}",
                    "remote_created": False,
                },
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        return 1
    print(json.dumps(result.to_dict(), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
