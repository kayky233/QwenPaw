"""Evidence-gated pull request and merge request delivery contracts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .contracts import ResearchArtifactType
from .step_repository import ResearchArtifactRepository


@dataclass(frozen=True)
class ChangeRequest:
    repository: str
    base_branch: str
    head_branch: str
    title: str
    body: str
    draft: bool = True


@dataclass(frozen=True)
class ChangeRequestResult:
    url: str
    number: int | None = None


class ChangeRequestProvider(Protocol):
    def create(self, request: ChangeRequest) -> ChangeRequestResult: ...


class EvidenceGatedDelivery:
    REQUIRED = (
        ResearchArtifactType.CODE_DIFF,
        ResearchArtifactType.TEST_RESULT,
        ResearchArtifactType.REPORT,
        ResearchArtifactType.COMMIT,
    )

    def __init__(
        self,
        artifacts: ResearchArtifactRepository,
        provider: ChangeRequestProvider,
    ) -> None:
        self.artifacts = artifacts
        self.provider = provider

    @staticmethod
    def _missing_message(missing: tuple[str, ...]) -> RuntimeError:
        return RuntimeError(
            "change request blocked; missing verified artifacts: "
            + ", ".join(missing)
        )

    def create(
        self,
        step_id: str,
        request: ChangeRequest,
    ) -> ChangeRequestResult:
        """Create using legacy same-step evidence semantics."""

        missing = tuple(
            artifact_type.value
            for artifact_type in self.REQUIRED
            if not self.artifacts.has_verified_type(step_id, artifact_type)
        )
        if missing:
            raise self._missing_message(missing)
        return self.provider.create(request)

    def create_for_run(
        self,
        run_id: str,
        request: ChangeRequest,
    ) -> ChangeRequestResult:
        """Create when verified evidence is distributed across run steps."""

        missing = tuple(
            artifact_type.value
            for artifact_type in self.REQUIRED
            if not self.artifacts.has_verified_type_for_run(
                run_id,
                artifact_type,
            )
        )
        if missing:
            raise self._missing_message(missing)
        return self.provider.create(request)
