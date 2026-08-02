from __future__ import annotations

from types import SimpleNamespace

from click.testing import CliRunner

from qwenpaw.cli import campaign_cmd as campaign_module
from qwenpaw.cli.campaign_entrypoint import campaign_cmd


class _Client:
    def __init__(self) -> None:
        self.wait_count = 0
        self.revisions = []

    def start(self, payload):
        self.payload = payload
        return {"campaign_id": "campaign-auto", "status": "accepted"}

    def wait(self, campaign_id, **kwargs):
        del kwargs
        self.wait_count += 1
        status = "needs_revision" if self.wait_count == 1 else "delivered"
        return SimpleNamespace(
            state={
                "campaign_id": campaign_id,
                "status": status,
                "repository": "owner/repository",
                "issue_number": 1,
                "events": [],
                "outcome": {
                    "delivery_mode": "draft_pr",
                    "revision_history": (
                        [] if status == "needs_revision" else [{"revision_number": 1}]
                    ),
                },
                "error": "ci_failed" if status == "needs_revision" else "",
            },
            elapsed_seconds=0.1,
        )

    def revise(self, campaign_id, **kwargs):
        self.revisions.append((campaign_id, kwargs))
        return {"status": "revision_accepted"}


def test_run_can_automatically_revise_same_campaign(monkeypatch, tmp_path) -> None:
    client = _Client()
    monkeypatch.setattr(campaign_module, "_client", lambda _: client)
    report = tmp_path / "result.json"
    runner = CliRunner()

    result = runner.invoke(
        campaign_cmd,
        [
            "run",
            "https://github.com/owner/repository/issues/1",
            "--allow",
            "src/fix.py",
            "--check",
            "pytest -q tests/test_fix.py",
            "--delivery",
            "draft_pr",
            "--auto-revise-rounds",
            "1",
            "--report",
            str(report),
        ],
        obj={"host": "127.0.0.1", "port": 8088},
    )

    assert result.exit_code == 0
    assert client.wait_count == 2
    assert client.revisions == [("campaign-auto", {})]
    assert "Starting automatic revision 1/1" in result.output
    assert report.is_file()
    assert report.with_suffix(".md").is_file()
