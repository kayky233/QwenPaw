"""Exact issue intake contracts for autonomous issue solving."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class IssueComment:
    author: str
    body: str
    created_at: str | None = None


@dataclass(frozen=True)
class IssueEvidence:
    repository: str
    number: int
    title: str
    body: str
    state: str
    labels: tuple[str, ...] = ()
    comments: tuple[IssueComment, ...] = ()
    linked_pull_requests: tuple[int, ...] = ()


class IssueProvider(Protocol):
    def fetch_issue(self, repository: str, number: int) -> IssueEvidence: ...


class GitHubIssueFetcher:
    """Adapter over an injected GitHub client.

    The client must expose ``get_issue`` and may expose ``get_issue_comments``.
    This keeps credentials and transport outside the research domain.
    """

    def __init__(self, client):
        self.client = client

    def fetch_issue(self, repository: str, number: int) -> IssueEvidence:
        raw = self.client.get_issue(repository, number)
        raw_comments = (
            self.client.get_issue_comments(repository, number)
            if hasattr(self.client, "get_issue_comments")
            else []
        )
        comments = tuple(
            IssueComment(
                author=str(item.get("author", "unknown")),
                body=str(item.get("body", "")),
                created_at=item.get("created_at"),
            )
            for item in raw_comments
        )
        return IssueEvidence(
            repository=repository,
            number=number,
            title=str(raw.get("title", "")),
            body=str(raw.get("body", "")),
            state=str(raw.get("state", "unknown")),
            labels=tuple(str(item) for item in raw.get("labels", ())),
            comments=comments,
            linked_pull_requests=tuple(
                int(item) for item in raw.get("linked_pull_requests", ())
            ),
        )
