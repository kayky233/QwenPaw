from __future__ import annotations

import json
from io import BytesIO

from qwenpaw.research_ledger.campaign_api_client import CampaignApiClient


class _Response:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


class _UrlOpen:
    def __init__(self):
        self.requests = []

    def __call__(self, request, timeout):
        self.requests.append((request, timeout))
        return _Response({"ok": True})


def _body(request):
    return json.loads((request.data or b"{}").decode("utf-8"))


def test_client_sends_revision_feedback_and_monitor_budget() -> None:
    opener = _UrlOpen()
    client = CampaignApiClient(
        "http://127.0.0.1:8088/api",
        agent_id="owner-agent",
        urlopen_func=opener,
    )

    client.revise(
        "campaign-1",
        feedback="fix the Windows check",
        monitor_attempts=7,
        monitor_interval_seconds=12.5,
    )

    request, _ = opener.requests[0]
    assert request.full_url.endswith("/research/campaigns/campaign-1/revise")
    assert request.method == "POST"
    assert request.headers["X-agent-id"] == "owner-agent"
    assert _body(request) == {
        "feedback": "fix the Windows check",
        "monitor_attempts": 7,
        "monitor_interval_seconds": 12.5,
    }


def test_client_uses_read_only_recovery_and_confirmed_cleanup() -> None:
    opener = _UrlOpen()
    client = CampaignApiClient(
        "http://127.0.0.1:8088/api",
        agent_id="default",
        urlopen_func=opener,
    )

    client.recovery("campaign-2")
    client.cleanup_worktree(
        "campaign-2",
        confirmation="CLEANUP_AUTORESEARCH_CAMPAIGN_WORKTREE",
    )

    recovery, _ = opener.requests[0]
    cleanup, _ = opener.requests[1]
    assert recovery.method == "GET"
    assert recovery.full_url.endswith("/research/campaigns/campaign-2/recovery")
    assert cleanup.method == "POST"
    assert cleanup.full_url.endswith(
        "/research/campaigns/campaign-2/cleanup-worktree"
    )
    assert _body(cleanup) == {
        "confirmation": "CLEANUP_AUTORESEARCH_CAMPAIGN_WORKTREE"
    }
