"""No-create provider used when revising an existing Draft PR or local receipt."""

from __future__ import annotations

from dataclasses import dataclass

from .change_request_delivery import ChangeRequest, ChangeRequestResult


@dataclass(frozen=True)
class ExistingChangeRequestIdentity:
    repository: str
    base_branch: str
    head_branch: str
    url: str
    number: int | None = None

    def validate(self) -> None:
        if not self.repository.strip():
            raise ValueError("existing change request repository is required")
        if not self.base_branch.strip():
            raise ValueError("existing change request base branch is required")
        if not self.head_branch.strip():
            raise ValueError("existing change request head branch is required")
        if not self.url.strip():
            raise ValueError("existing change request URL is required")


class ExistingChangeRequestProvider:
    """Return one known PR/MR identity without creating another request."""

    def __init__(self, identity: ExistingChangeRequestIdentity) -> None:
        identity.validate()
        self.identity = identity
        self.requests: list[ChangeRequest] = []

    def create(self, request: ChangeRequest) -> ChangeRequestResult:
        identity = self.identity
        if request.repository.casefold() != identity.repository.casefold():
            raise RuntimeError(
                "revision delivery repository does not match the existing request"
            )
        if request.base_branch != identity.base_branch:
            raise RuntimeError(
                "revision delivery base branch does not match the existing request"
            )
        if request.head_branch != identity.head_branch:
            raise RuntimeError(
                "revision delivery head branch does not match the existing request"
            )
        if not request.draft:
            raise RuntimeError("revision delivery must preserve Draft status")
        self.requests.append(request)
        return ChangeRequestResult(url=identity.url, number=identity.number)
