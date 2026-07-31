from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from qwenpaw.cli import campaign_cmd as campaign_module
from qwenpaw.research_ledger.campaign_api_client import CampaignWaitResult


class _Client:
    started_payload = None
    remote_delivery_available = True

    def __init__(self, *args, **kwargs) -> None:
        pass

    def info(self):
        return {
            "available": True,
            "unsafe_execution_enabled": True,
            "delivery_modes": ["local", "draft_pr"],
            "default_delivery_mode": "local",
            "automatic_merge": False,
            "git_available": True,
            "github_cli_available": True,
            "github_token_available": False,
            "remote_delivery_available": type(self).remote_delivery_available,
            "agents": [
                {
                    "id": "coder",
                    "workspace_dir": "/tmp/coder",
                    "startup_status": "running",
                },
                {
                    "id": "reviewer",
                    "workspace_dir": "/tmp/reviewer",
                    "startup_status": "running",
                },
            ],
        }

    def start(self, payload):
        type(self).started_payload = payload
        return {"campaign_id": "run-1", "status": "accepted"}

    def wait(self, campaign_id, **kwargs):
        callback = kwargs.get("on_event")
        if callback:
            callback({"sequence": 1, "phase": "implementing", "detail": "code"})
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
            elapsed_seconds=1.0,
        )

    def get(self, campaign_id):
        return {"campaign_id": campaign_id, "status": "running"}

    def cancel(self, campaign_id):
        return {"campaign_id": campaign_id, "status": "cancelled"}


def test_campaign_init_writes_reusable_manifest(tmp_path: Path) -> None:
    output = tmp_path / "campaign.json"
    result = CliRunner().invoke(
        campaign_module.campaign_cmd,
        [
            "init",
            "https://github.com/owner/repository/issues/7",
            "--output",
            str(output),
            "--allow",
            "src/fix.py",
            "--check",
            "pytest -q tests/test_fix.py",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["delivery_mode"] == "local"
    assert payload["modifiable_files"] == ["src/fix.py"]


def test_campaign_run_starts_waits_and_writes_reports(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(campaign_module, "CampaignApiClient", _Client)
    report = tmp_path / "result.json"

    result = CliRunner().invoke(
        campaign_module.campaign_cmd,
        [
            "run",
            "https://github.com/owner/repository/issues/7",
            "--allow",
            "src/fix.py",
            "--allow",
            "tests/test_fix.py",
            "--check",
            "pytest -q tests/test_fix.py",
            "--implementer",
            "coder",
            "--reviewer",
            "reviewer",
            "--report",
            str(report),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "Campaign run-1 accepted" in result.output
    assert "[implementing] code" in result.output
    assert report.is_file()
    assert report.with_suffix(".md").is_file()
    assert _Client.started_payload["delivery_mode"] == "local"


def test_campaign_doctor_validates_selected_agents(monkeypatch) -> None:
    monkeypatch.setattr(campaign_module, "CampaignApiClient", _Client)
    _Client.remote_delivery_available = True

    result = CliRunner().invoke(
        campaign_module.campaign_cmd,
        [
            "doctor",
            "--implementer",
            "coder",
            "--reviewer",
            "reviewer",
        ],
    )

    assert result.exit_code == 0, result.output
    assert '"ready": true' in result.output


def test_campaign_doctor_uses_server_remote_readiness(monkeypatch) -> None:
    monkeypatch.setattr(campaign_module, "CampaignApiClient", _Client)
    _Client.remote_delivery_available = False

    result = CliRunner().invoke(
        campaign_module.campaign_cmd,
        [
            "doctor",
            "--delivery",
            "draft_pr",
            "--implementer",
            "coder",
            "--reviewer",
            "reviewer",
        ],
    )

    assert result.exit_code != 0
    assert "GitHub token" in result.output
