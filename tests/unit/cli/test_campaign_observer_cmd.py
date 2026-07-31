from __future__ import annotations

from click.testing import CliRunner

from qwenpaw.cli import campaign_observer_cmd as module
from qwenpaw.research_ledger.campaign_api_client import CampaignWaitResult


class _Client:
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
            state={
                "campaign_id": campaign_id,
                "status": "delivered",
                "repository": "owner/repository",
                "issue_number": 7,
                "worktree_path": "/tmp/worktree",
                "branch": "autoresearch/issue-7-run1",
                "error": "",
                "outcome": {
                    "delivery_mode": "local",
                    "delivery": {
                        "commit_sha": "a" * 40,
                        "url": "local://autoresearch/run-1/draft-change-request",
                    },
                    "artifacts": [],
                },
            },
            elapsed_seconds=2.0,
        )


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
