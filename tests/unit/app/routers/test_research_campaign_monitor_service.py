from __future__ import annotations

from types import SimpleNamespace

import pytest

from qwenpaw.app.routers import research_campaign_monitor_service as service
from qwenpaw.research_ledger.delivery_lifecycle import (
    CICheck,
    CICheckStatus,
    CIReport,
)
from qwenpaw.research_ledger.github_delivery_monitor import (
    GitHubDeliverySnapshot,
)


@pytest.mark.asyncio
async def test_monitor_reports_unavailable_without_gh(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(service.shutil, "which", lambda _: None)

    result = await service._monitor_delivery(
        SimpleNamespace(),
        repository="owner/repository",
        pr_url="https://github.com/owner/repository/pull/1",
        commit_sha="a" * 40,
        worktree=tmp_path,
    )

    assert result["status"] == "monitor_unavailable"
    assert result["automatic_merge"] is False


@pytest.mark.asyncio
async def test_monitor_propagates_failed_ci(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(service.shutil, "which", lambda _: "/usr/bin/gh")

    async def fetch(**kwargs):
        return GitHubDeliverySnapshot(
            ci_report=CIReport(
                commit_sha="a" * 40,
                checks=(CICheck("unit", CICheckStatus.FAILED),),
            ),
            review_threads=(),
            review_decision="REVIEW_REQUIRED",
        )

    monkeypatch.setattr(service, "fetch_github_delivery_snapshot", fetch)
    result = await service._monitor_delivery(
        SimpleNamespace(_run_process=None),
        repository="owner/repository",
        pr_url="https://github.com/owner/repository/pull/1",
        commit_sha="a" * 40,
        worktree=tmp_path,
    )

    assert result["status"] == "needs_revision"
    assert result["attempts"][0]["checks"][0]["status"] == "failed"
    assert result["attempts"][0]["blockers"] == ["ci_failed"]


@pytest.mark.asyncio
async def test_monitor_requires_verified_commit_identity(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(service.shutil, "which", lambda _: "/usr/bin/gh")

    async def fetch(**kwargs):
        return GitHubDeliverySnapshot(
            ci_report=CIReport(
                commit_sha="b" * 40,
                checks=(CICheck("unit", CICheckStatus.PASSED),),
            ),
            review_threads=(),
            review_decision="APPROVED",
        )

    monkeypatch.setattr(service, "fetch_github_delivery_snapshot", fetch)
    result = await service._monitor_delivery(
        SimpleNamespace(_run_process=None),
        repository="owner/repository",
        pr_url="https://github.com/owner/repository/pull/1",
        commit_sha="a" * 40,
        worktree=tmp_path,
    )

    assert result["status"] == "needs_revision"
    assert result["attempts"][0]["blockers"] == ["commit_sha_mismatch"]
