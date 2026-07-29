"""Evidence and scope gate for reproducible AutoResearch episodes."""

from __future__ import annotations

from dataclasses import dataclass

from .contracts import ResearchArtifactContract, ResearchArtifactType
from .episode_package import EpisodePackage


@dataclass(frozen=True)
class EpisodeEvidenceResult:
    ready: bool
    missing_artifacts: tuple[str, ...]
    invalid_artifacts: tuple[str, ...]
    scope_violations: tuple[str, ...]


class EpisodeEvidenceGate:
    def evaluate(
        self,
        episode: EpisodePackage,
        artifacts: tuple[ResearchArtifactContract, ...],
    ) -> EpisodeEvidenceResult:
        episode.validate()
        invalid: list[str] = []
        scope_violations: set[str] = set()
        verified_types: set[ResearchArtifactType] = set()
        allowed_paths = set(episode.modifiable_files)

        for artifact in artifacts:
            if artifact.run_id != episode.run_id:
                invalid.append(
                    f"{artifact.artifact_id}:run_id_mismatch"
                )
                continue
            if artifact.verified:
                verified_types.add(artifact.artifact_type)
            if artifact.artifact_type != ResearchArtifactType.CODE_DIFF:
                continue
            changed_paths = artifact.metadata.get("changed_paths")
            if not isinstance(changed_paths, (list, tuple)) or not changed_paths:
                invalid.append(
                    f"{artifact.artifact_id}:changed_paths_missing"
                )
                continue
            for path in changed_paths:
                normalized = str(path).replace("\\", "/")
                if normalized not in allowed_paths:
                    scope_violations.add(normalized)

        required_types = {
            item.artifact_type
            for item in episode.expected_artifacts
            if item.required
        }
        missing = tuple(
            item.value
            for item in sorted(required_types - verified_types, key=lambda value: value.value)
        )
        violations = tuple(sorted(scope_violations))
        invalid_items = tuple(dict.fromkeys(invalid))
        return EpisodeEvidenceResult(
            ready=not missing and not invalid_items and not violations,
            missing_artifacts=missing,
            invalid_artifacts=invalid_items,
            scope_violations=violations,
        )
