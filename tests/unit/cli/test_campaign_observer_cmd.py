from __future__ import annotations

from click.testing import CliRunner

from qwenpaw.cli import campaign_observer_cmd as module
from qwenpaw.research_ledger.campaign_api_client import CampaignWaitResult


def _delivered_state(*, lifecycle_status: str = "review_waiting") -> dict:
    return {
        "campaign_id": "run-1",
        "status": "delivered",
        "repository": "owner/repository",
        "issue_number": 7,
        "worktree_path": "/tmp/worktree",
        "branch": "autoresearch/issue-7-run1",
        "error": "",
        "outcome": {
            "delivery_mode": "draft_pr",
            "delivery": {
                "commit_sha": "a" * 40,
                "url": "https://github.com/owner/repository/pull/9",
            },
            "delivery_lifecycle": {
                "status": lifecycle_status,
                "reason": "CI passed",
                "attempts": [],
            },
            "artifacts": [],
        },
    }


class _Client:
    refresh_state = _delivered_state()

    def __init__(self, *args, **kwargs) -> None:
        pass

    def history(self, *, limit, status):
        return {
            "count": 1,
            "total_matching": 1,
            "items": [
                {
                    "campaign_id": "run-1",
                    "status": status or "delivered",
                }
            ],
        }

    def wait(self, campaign_id, **kwargs):
        callback = kwargs.get("on_event")
        if callback:
            callback(
                {
                    "sequence": 3,
                    "phase": "review_waiting",
                    "detail": "CI passed",
                }
            )
        return CampaignWaitResult(
            state=_delivered_state(),
            elapsed_seconds=2.0,
        )

    def refresh_delivery(self, campaign_id):
        assert campaign_id == "run-1"
        return type(self).refresh_state


def test_campaign_history_command(monkeypatch) -> None:
    monkeypatch.setattr(module, "CampaignApiClient", _Client)

    result = CliRunner().invoke(
        module.campaign_history_cmd,
        ["--limit", "5", "--status", "delivered"],
    )

    assert result.exit_code == 0, result.output
    assert '"campaign_id": "run-1"' in result.output


def test_campaign_watch_reconnects_and_writes_report(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(module, "CampaignApiClient", _Client)
    report = tmp_path / "result.json"

    result = CliRunner().invoke(
        module.campaign_watch_cmd,
        ["run-1", "--report", str(report)],
    )

    assert result.exit_code == 0, result.output
    assert "[review_waiting] CI passed" in result.output
    assert report.is_file()
    assert report.with_suffix(".md").is_file()


def test_campaign_refresh_updates_report(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(module, "CampaignApiClient", _Client)
    _Client.refresh_state = _delivered_state(lifecycle_status="merge_ready")
    report = tmp_path / "refresh.json"

    result = CliRunner().invoke(
        module.campaign_refresh_cmd,
        ["run-1", "--report", str(report)],
    )

    assert result.exit_code == 0, result.output
    assert '"status": "merge_ready"' in result.output
    assert report.is_file()
    assert "Delivery lifecycle: `merge_ready`" in report.with_suffix(
        ".md"
    ).read_text(encoding="utf-8")


def test_campaign_refresh_returns_nonzero_for_needs_revision(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(module, "CampaignApiClient", _Client)
    state = _delivered_state(lifecycle_status="needs_revision")
    state["status"] = "needs_revision"
    state["error"] = "CI failed"
    _Client.refresh_state = state

    result = CliRunner().invoke(
        module.campaign_refresh_cmd,
        ["run-1", "--report", str(tmp_path / "failed.json")],
    )

    assert result.exit_code != 0
    assert "needs revision" in result.output
