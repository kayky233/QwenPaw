from __future__ import annotations

import json

import pytest

from qwenpaw.app.routers.research_campaign_runtime import (
    _extract_review_payload,
    _fetch_github_issue,
    _prepared_issue,
)
from qwenpaw.research_ledger.issue_fetcher import IssueEvidence


class Response:
    def __init__(self, payload):
        self.status = 200
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


class UrlOpen:
    def __init__(self):
        self.requests = []

    def __call__(self, request, timeout):
        self.requests.append((request, timeout))
        if request.full_url.endswith("/comments"):
            return Response(
                [
                    {
                        "user": {"login": "alice"},
                        "body": "Please keep compatibility",
                        "created_at": "2026-07-29T00:00:00Z",
                    }
                ]
            )
        return Response(
            {
                "title": "Fix cache expiry",
                "body": "Expired entries remain visible",
                "state": "open",
                "labels": [{"name": "bug"}],
            }
        )


def test_review_payload_accepts_only_explicit_verdict_json():
    payload = _extract_review_payload(
        "Review complete.\n```json\n"
        '{"verdict":"approve","findings":[],"required_changes":[]}'
        "\n```"
    )

    assert payload["verdict"] == "approve"


@pytest.mark.parametrize(
    "text",
    [
        "looks good",
        '{"verdict":"maybe","findings":[],"required_changes":[]}',
        '{"verdict":"approve","findings":"none","required_changes":[]}',
    ],
)
def test_review_payload_rejects_ambiguous_or_invalid_responses(text):
    with pytest.raises(RuntimeError):
        _extract_review_payload(text)


def test_explicit_scope_builds_prepared_issue_without_graph():
    evidence = IssueEvidence(
        repository="owner/repo",
        number=12,
        title="Fix cache expiry",
        body="Expired entries remain visible",
        state="open",
    )

    prepared = _prepared_issue(
        evidence,
        (
            "src/qwenpaw/memory/cache.py",
            "tests/unit/test_cache.py",
        ),
    )

    context = prepared.contextual_plan.context_pack
    assert context.affected_paths == ("src/qwenpaw/memory/cache.py",)
    assert context.test_paths == ("tests/unit/test_cache.py",)
    assert context.downgrade_reason == "explicit_scope_approval"


def test_github_issue_fetch_normalizes_labels_and_comments():
    opener = UrlOpen()

    evidence = _fetch_github_issue(
        "owner/repo",
        12,
        "token",
        opener,
    )

    assert evidence.title == "Fix cache expiry"
    assert evidence.labels == ("bug",)
    assert evidence.comments[0].author == "alice"
    assert len(opener.requests) == 2
    assert opener.requests[0][0].headers["Authorization"] == "Bearer token"
