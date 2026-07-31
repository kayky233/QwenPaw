from __future__ import annotations

import json
from pathlib import Path

from qwenpaw.research_ledger.campaign_api_client import (
    CampaignApiClient,
    write_campaign_report,
)


class _Response:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


def test_client_starts_and_reads_campaign_with_agent_header() -> None:
    captured = []
    responses = iter(
        [
            _Response({"campaign_id": "run-1", "status": "accepted"}),
            _Response({"campaign_id": "run-1", "status": "delivered"}),
        ]
    )

    def open_request(request, *, timeout):
        captured.append((request, timeout))
        return next(responses)

    client = CampaignApiClient(
        "http://127.0.0.1:8088/api",
        agent_id="default",
        urlopen_func=open_request,
    )

    started = client.start({"repository": "owner/repository"})
    state = client.get("run-1")

    assert started["campaign_id"] == "run-1"
    assert state["status"] == "delivered"
    assert captured[0][0].full_url.endswith("/research/campaigns/run")
    assert captured[0][0].get_header("X-agent-id") == "default"


def test_client_wait_replays_each_event_once(monkeypatch) -> None:
    states = iter(
        [
            {
                "campaign_id": "run-1",
                "status": "running",
                "events": [
                    {"sequence": 1, "phase": "accepted", "detail": "ready"},
                ],
            },
            {
                "campaign_id": "run-1",
                "status": "delivered",
                "events": [
                    {"sequence": 1, "phase": "accepted", "detail": "ready"},
                    {"sequence": 2, "phase": "delivered", "detail": "done"},
                ],
            },
        ]
    )
    client = CampaignApiClient("http://127.0.0.1:8088/api")
    monkeypatch.setattr(client, "get", lambda _: next(states))
    monkeypatch.setattr("time.sleep", lambda _: None)
    events = []

    result = client.wait(
        "run-1",
        timeout_seconds=10,
        poll_interval_seconds=0.1,
        on_event=events.append,
    )

    assert [item["sequence"] for item in events] == [1, 2]
    assert result.state["status"] == "delivered"


def test_report_contains_delivery_and_artifact_evidence(tmp_path: Path) -> None:
    state = {
        "campaign_id": "run-1",
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
            "artifacts": [
                {
                    "artifact_type": "code_diff",
                    "step_id": "run-1-implement",
                    "verified": True,
                    "content_hash": "b" * 64,
                }
            ],
        },
    }

    json_path, markdown_path = write_campaign_report(
        state,
        tmp_path,
        elapsed_seconds=1.5,
    )

    assert json.loads(json_path.read_text())["cli_elapsed_seconds"] == 1.5
    markdown = markdown_path.read_text(encoding="utf-8")
    assert "Delivery mode: `local`" in markdown
    assert "local://autoresearch/run-1" in markdown
    assert "code_diff" in markdown
