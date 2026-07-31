from __future__ import annotations

import json
from pathlib import Path

import pytest

from qwenpaw.research_ledger.change_request_delivery import (
    ChangeRequest,
    ChangeRequestResult,
)
from qwenpaw.research_ledger.contracts import ResearchArtifactType
from qwenpaw.research_ledger.episode_package import (
    EpisodeCommand,
    EpisodeExpectedArtifact,
    EpisodePackage,
)
from qwenpaw.research_ledger.guarded_remote_campaign_e2e import (
    RepositoryBoundDraftProvider,
    _branch_must_not_exist,
    run_guarded_remote_draft_pr_e2e,
)
from qwenpaw.research_ledger.remote_campaign_e2e import (
    REMOTE_CONFIRMATION,
    RemoteCampaignE2EResult,
    RemoteE2EPolicy,
    RemoteMonitorAttempt,
)


class _Provider:
    def __init__(self, result: ChangeRequestResult) -> None:
        self.result = result
        self.request: ChangeRequest | None = None

    def create(self, request: ChangeRequest) -> ChangeRequestResult:
        self.request = request
        return self.result


def _episode(path: Path) -> EpisodePackage:
    package = EpisodePackage(
        episode_id="episode-guarded",
        run_id="guarded-run",
        task_type="bug_fix",
        repository="owner/e2e-repo",
        issue_number=11,
        goal="Guard the remote E2E flow",
        base_revision="a" * 40,
        acceptance_criteria=("Draft-only delivery remains enforced",),
        modifiable_files=("src/fix.py",),
        commands=(
            EpisodeCommand(
                command_id="unit",
                stage="unit",
                argv=("pytest", "-q", "tests/test_fix.py"),
            ),
        ),
        expected_artifacts=(
            EpisodeExpectedArtifact(ResearchArtifactType.PLAN, "plan"),
        ),
    )
    path.write_text(package.to_json(), encoding="utf-8")
    return package


def test_repository_bound_provider_rejects_wrong_pr_repository() -> None:
    provider = RepositoryBoundDraftProvider(
        "owner/e2e-repo",
        _Provider(
            ChangeRequestResult(
                url="https://github.com/other/repo/pull/7",
                number=7,
            )
        ),
    )
    with pytest.raises(RuntimeError, match="does not match"):
        provider.create(
            ChangeRequest(
                repository="owner/e2e-repo",
                base_branch="main",
                head_branch="autoresearch/e2e/run",
                title="test",
                body="body",
                draft=True,
            )
        )


@pytest.mark.asyncio
async def test_remote_branch_collision_blocks_before_execution(
    tmp_path: Path,
) -> None:
    async def run_process(argv, *, cwd, timeout):
        del argv, cwd, timeout
        return "deadbeef\trefs/heads/autoresearch/e2e/run\n"

    with pytest.raises(RuntimeError, match="already exists"):
        await _branch_must_not_exist(
            tmp_path,
            "autoresearch/e2e/run",
            run_process,
        )


@pytest.mark.asyncio
async def test_guarded_entry_maps_green_draft_to_draft_validated(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    episode_path = tmp_path / "episode.json"
    _episode(episode_path)
    report_dir = tmp_path / "report"
    report_dir.mkdir()

    async def run_process(argv, *, cwd, timeout):
        del argv, cwd, timeout
        return ""

    async def fake_remote(*args, **kwargs):
        del args, kwargs
        result = RemoteCampaignE2EResult(
            status="merge_ready",
            repository="owner/e2e-repo",
            issue_number=11,
            branch="autoresearch/e2e/guarded-run",
            commit_sha="b" * 40,
            pull_request_url="https://github.com/owner/e2e-repo/pull/12",
            pull_request_number=12,
            draft=True,
            automatic_merge=False,
            local_verification_report=str(tmp_path / "local.md"),
            remote_report_json=str(report_dir / "remote-verification.json"),
            remote_report_markdown=str(report_dir / "remote-verification.md"),
            monitor_attempts=(
                RemoteMonitorAttempt(
                    attempt=1,
                    status="merge_ready",
                    review_decision="APPROVED",
                    commit_sha="b" * 40,
                    checks=(
                        {
                            "name": "unit",
                            "status": "passed",
                            "url": "",
                            "summary": "",
                        },
                    ),
                ),
            ),
        )
        Path(result.remote_report_json).write_text(
            json.dumps(
                {
                    "result": result.to_dict(),
                    "policy": {},
                    "issue": {
                        "repository": "owner/e2e-repo",
                        "number": 11,
                        "state": "open",
                        "labels": ["autoresearch-e2e"],
                        "title": "E2E",
                    },
                }
            ),
            encoding="utf-8",
        )
        Path(result.remote_report_markdown).write_text("old", encoding="utf-8")
        return result

    monkeypatch.setattr(
        "qwenpaw.research_ledger.guarded_remote_campaign_e2e."
        "run_remote_draft_pr_e2e",
        fake_remote,
    )
    result = await run_guarded_remote_draft_pr_e2e(
        tmp_path,
        episode_path,
        tmp_path / "candidate.patch",
        report_dir,
        base_branch="main",
        confirmation=REMOTE_CONFIRMATION,
        policy=RemoteE2EPolicy(
            allowed_repositories=("owner/e2e-repo",),
        ),
        run_process=run_process,
    )

    assert result.status == "draft_validated"
    assert result.automatic_merge is False
    assert result.monitor_attempts[-1].blockers == (
        "draft_pr_requires_human_ready",
    )
    payload = json.loads(
        Path(result.remote_report_json).read_text(encoding="utf-8")
    )
    assert payload["result"]["status"] == "draft_validated"
