"""Platform adapters for GitHub pull requests and GitLab merge requests."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

from .change_request_delivery import ChangeRequest, ChangeRequestResult


class GitHubPullRequestClient(Protocol):
    def create_pull_request(
        self,
        *,
        repository: str,
        base: str,
        head: str,
        title: str,
        body: str,
        draft: bool,
    ) -> Mapping[str, Any]: ...


class GitLabMergeRequestClient(Protocol):
    def create_merge_request(
        self,
        *,
        project: str,
        target_branch: str,
        source_branch: str,
        title: str,
        description: str,
        draft: bool,
    ) -> Mapping[str, Any]: ...


class GitHubChangeRequestProvider:
    def __init__(self, client: GitHubPullRequestClient):
        self.client = client

    def create(self, request: ChangeRequest) -> ChangeRequestResult:
        _validate_request(request)
        response = self.client.create_pull_request(
            repository=request.repository,
            base=request.base_branch,
            head=request.head_branch,
            title=request.title,
            body=request.body,
            draft=request.draft,
        )
        return _result_from_mapping(
            response,
            url_keys=("html_url", "url"),
            number_keys=("number", "id"),
            platform="GitHub",
        )


class GitLabChangeRequestProvider:
    def __init__(self, client: GitLabMergeRequestClient):
        self.client = client

    def create(self, request: ChangeRequest) -> ChangeRequestResult:
        _validate_request(request)
        response = self.client.create_merge_request(
            project=request.repository,
            target_branch=request.base_branch,
            source_branch=request.head_branch,
            title=request.title,
            description=request.body,
            draft=request.draft,
        )
        return _result_from_mapping(
            response,
            url_keys=("web_url", "url"),
            number_keys=("iid", "number", "id"),
            platform="GitLab",
        )


def _validate_request(request: ChangeRequest) -> None:
    required = {
        "repository": request.repository,
        "base_branch": request.base_branch,
        "head_branch": request.head_branch,
        "title": request.title,
        "body": request.body,
    }
    for name, value in required.items():
        if not str(value).strip():
            raise ValueError(f"change request {name} is required")
    if request.base_branch == request.head_branch:
        raise ValueError("change request base and head branches must differ")


def _result_from_mapping(
    response: Mapping[str, Any],
    *,
    url_keys: tuple[str, ...],
    number_keys: tuple[str, ...],
    platform: str,
) -> ChangeRequestResult:
    url = next(
        (
            str(response[key]).strip()
            for key in url_keys
            if response.get(key) is not None and str(response[key]).strip()
        ),
        "",
    )
    if not url:
        raise RuntimeError(f"{platform} did not return a change request URL")

    number: int | None = None
    for key in number_keys:
        value = response.get(key)
        if value is None:
            continue
        try:
            number = int(value)
        except (TypeError, ValueError):
            continue
        break
    return ChangeRequestResult(url=url, number=number)
