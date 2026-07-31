"""Local change-request receipts for non-remote Campaign delivery."""

from __future__ import annotations

from .change_request_delivery import ChangeRequest, ChangeRequestResult


class LocalChangeRequestProvider:
    """Record a delivery request without contacting a remote service."""

    def __init__(self, run_id: str) -> None:
        if not run_id.strip():
            raise ValueError("run_id is required")
        self.run_id = run_id
        self.request: ChangeRequest | None = None

    def create(self, request: ChangeRequest) -> ChangeRequestResult:
        if not request.draft:
            raise ValueError("local Campaign receipts must remain draft")
        self.request = request
        return ChangeRequestResult(
            url=f"local://autoresearch/{self.run_id}/draft-change-request",
            number=None,
        )
