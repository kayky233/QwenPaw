from __future__ import annotations

import pytest

from qwenpaw.research_ledger.change_request_delivery import ChangeRequest
from qwenpaw.research_ledger.existing_change_request import (
    ExistingChangeRequestIdentity,
    ExistingChangeRequestProvider,
)


def _request(**overrides):
    payload = {
        "repository": "owner/repository",
        "base_branch": "main",
        "head_branch": "owner:autoresearch/issue-7-run",
        "title": "fix: regression",
        "body": "verified revision",
        "draft": True,
    }
    payload.update(overrides)
    return ChangeRequest(**payload)


def test_existing_provider_returns_same_request_without_creating_another() -> None:
    provider = ExistingChangeRequestProvider(
        ExistingChangeRequestIdentity(
            repository="owner/repository",
            base_branch="main",
            head_branch="owner:autoresearch/issue-7-run",
            url="https://github.com/owner/repository/pull/19",
            number=19,
        )
    )

    result = provider.create(_request())

    assert result.url == "https://github.com/owner/repository/pull/19"
    assert result.number == 19
    assert provider.requests == [_request()]


@pytest.mark.parametrize(
    ("request", "message"),
    [
        (_request(repository="other/repository"), "repository"),
        (_request(base_branch="release"), "base branch"),
        (_request(head_branch="other:branch"), "head branch"),
        (_request(draft=False), "Draft status"),
    ],
)
def test_existing_provider_blocks_identity_or_draft_changes(
    request: ChangeRequest,
    message: str,
) -> None:
    provider = ExistingChangeRequestProvider(
        ExistingChangeRequestIdentity(
            repository="owner/repository",
            base_branch="main",
            head_branch="owner:autoresearch/issue-7-run",
            url="https://github.com/owner/repository/pull/19",
            number=19,
        )
    )

    with pytest.raises(RuntimeError, match=message):
        provider.create(request)
