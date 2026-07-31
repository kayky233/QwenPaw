from __future__ import annotations

import json
from pathlib import Path

import pytest

from qwenpaw.research_ledger.remote_campaign_control import (
    CLEANUP_CONFIRMATION,
    PROMOTE_CONFIRMATION,
    RemotePullRequestState,
    cleanup_remote_e2e_pull_request,
    promote_remote_e2e_draft,
)


class _Client:
    def __init__(self, state: RemotePullRequestState) -> None:
        self.state = state
        self.ready_calls: list[tuple[str, str]] = []
        self.cleanup_calls: list[tuple[str, str]] = []

    def inspect(
        self,
        repository: str,
        pull_request_url: str,
    ) -> RemotePullRequestState:
        assert repository == self.state.repository
        assert pull_request_url == self.state.url
        return self.state

    def mark_ready(self, repository: str, pull_request_url: str) -> None:
        self.ready_calls.append((repository, pull_request_url))

    def close_and_delete_branch(
        self,
        repository: str,
        pull_request_url: str,
    ) -> None:
        self.cleanup_calls.append((repository, pull_request_url))


def _report(path: Path, *, status: str = "draft_validated") -> None:
    path.write_text(
        json.dumps(
            {
                "result": {
                    "status": status,
                    "repository": "owner/e2e-repo",
                    "issue_number": 4,
                    "branch": "autoresearch/e2e/run-4",
                    "commit_sha": "c" * 40,
                    "pull_request_url": (
                        "https://github.com/owner/e2e-repo/pull/19"
                    ),
                    "pull_request_number": 19,
                    "draft": True,
                    "automatic_merge": False,
                    "monitor_attempts": [],
                }
            }
        ),
        encoding="utf-8",
    )


def _state(**overrides) -> RemotePullRequestState:
    values = {
        "repository": "owner/e2e-repo",
        "url": "https://github.com/owner/e2e-repo/pull/19",
        "number": 19,
        "state": "OPEN",
        "draft": True,
        "head_branch": "autoresearch/e2e/run-4",
        "head_commit": "c" * 40,
        "title": "test(autoresearch): remote E2E",
        "body": "## AutoResearch guarded remote E2E",
        "review_decision": "REVIEW_REQUIRED",
        "checks": (
            {
                "name": "unit",
                "status": "COMPLETED",
                "conclusion": "SUCCESS",
            },
        ),
    }
    values.update(overrides)
    return RemotePullRequestState(**values)


def test_promote_ready_requires_second_confirmation_and_green_ci(
    tmp_path: Path,
) -> None:
    report = tmp_path / "remote-verification.json"
    _report(report)
    client = _Client(_state())

    with pytest.raises(RuntimeError, match="confirmation mismatch"):
        promote_remote_e2e_draft(
            report,
            confirmation="no",
            allowed_repositories=("owner/e2e-repo",),
            client=client,
        )

    result = promote_remote_e2e_draft(
        report,
        confirmation=PROMOTE_CONFIRMATION,
        allowed_repositories=("owner/e2e-repo",),
        client=client,
    )
    assert result.status == "ready_for_human_merge"
    assert result.automatic_merge is False
    assert client.ready_calls == [
        ("owner/e2e-repo", "https://github.com/owner/e2e-repo/pull/19")
    ]


def test_promote_ready_rejects_commit_mismatch(tmp_path: Path) -> None:
    report = tmp_path / "remote-verification.json"
    _report(report)
    client = _Client(_state(head_commit="d" * 40))

    with pytest.raises(RuntimeError, match="HEAD no longer matches"):
        promote_remote_e2e_draft(
            report,
            confirmation=PROMOTE_CONFIRMATION,
            allowed_repositories=("owner/e2e-repo",),
            client=client,
        )
    assert client.ready_calls == []


def test_cleanup_only_closes_known_marked_e2e_pr(tmp_path: Path) -> None:
    report = tmp_path / "remote-verification.json"
    _report(report)
    client = _Client(_state())

    result = cleanup_remote_e2e_pull_request(
        report,
        confirmation=CLEANUP_CONFIRMATION,
        allowed_repositories=("owner/e2e-repo",),
        client=client,
    )
    assert result.status == "closed_and_branch_deleted"
    assert result.automatic_merge is False
    assert client.cleanup_calls == [
        ("owner/e2e-repo", "https://github.com/owner/e2e-repo/pull/19")
    ]


def test_cleanup_refuses_unmarked_pr(tmp_path: Path) -> None:
    report = tmp_path / "remote-verification.json"
    _report(report)
    client = _Client(_state(body="ordinary pull request"))

    with pytest.raises(RuntimeError, match="missing the AutoResearch E2E marker"):
        cleanup_remote_e2e_pull_request(
            report,
            confirmation=CLEANUP_CONFIRMATION,
            allowed_repositories=("owner/e2e-repo",),
            client=client,
        )
    assert client.cleanup_calls == []


def test_control_client_contract_exposes_no_merge_method() -> None:
    client = _Client(_state())
    assert not hasattr(client, "merge")
    assert not hasattr(client, "auto_merge")
