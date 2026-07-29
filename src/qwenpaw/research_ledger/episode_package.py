"""Portable, reproducible episode packages for AutoResearch campaigns."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from hashlib import sha256
from pathlib import PurePosixPath
from typing import Any

from .contracts import ResearchArtifactType


@dataclass(frozen=True)
class EpisodeCommand:
    command_id: str
    stage: str
    argv: tuple[str, ...]
    cwd: str = "."
    timeout_seconds: float = 300.0
    required: bool = True

    def validate(self) -> None:
        if not self.command_id.strip():
            raise ValueError("command_id is required")
        if not self.stage.strip():
            raise ValueError("command stage is required")
        if not self.argv or not all(str(item).strip() for item in self.argv):
            raise ValueError(f"command {self.command_id!r} requires argv")
        if self.timeout_seconds <= 0:
            raise ValueError(
                f"command {self.command_id!r} timeout must be positive"
            )
        _validate_relative_path(self.cwd, label="command cwd", allow_dot=True)


@dataclass(frozen=True)
class EpisodeExpectedArtifact:
    artifact_type: ResearchArtifactType
    producer_step: str
    required: bool = True
    verifier: str = "host"

    def validate(self) -> None:
        if not self.producer_step.strip():
            raise ValueError("artifact producer_step is required")
        if not self.verifier.strip():
            raise ValueError("artifact verifier is required")


@dataclass(frozen=True)
class EpisodePackage:
    episode_id: str
    run_id: str
    task_type: str
    repository: str
    goal: str
    base_revision: str
    acceptance_criteria: tuple[str, ...]
    modifiable_files: tuple[str, ...]
    commands: tuple[EpisodeCommand, ...]
    expected_artifacts: tuple[EpisodeExpectedArtifact, ...]
    issue_number: int | None = None
    frozen_files: tuple[str, ...] = ()
    environment: dict[str, str] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    schema_version: int = 1

    def validate(self) -> None:
        required_text = {
            "episode_id": self.episode_id,
            "run_id": self.run_id,
            "task_type": self.task_type,
            "repository": self.repository,
            "goal": self.goal,
            "base_revision": self.base_revision,
        }
        for name, value in required_text.items():
            if not str(value).strip():
                raise ValueError(f"{name} is required")
        if self.schema_version != 1:
            raise ValueError(
                f"unsupported episode schema_version: {self.schema_version}"
            )
        if self.issue_number is not None and self.issue_number <= 0:
            raise ValueError("issue_number must be positive")
        if not self.acceptance_criteria:
            raise ValueError("at least one acceptance criterion is required")
        if not all(item.strip() for item in self.acceptance_criteria):
            raise ValueError("acceptance criteria cannot be blank")
        if not self.modifiable_files:
            raise ValueError("at least one modifiable file is required")
        if not self.commands:
            raise ValueError("at least one validation command is required")
        if not self.expected_artifacts:
            raise ValueError("at least one expected artifact is required")

        modifiable = _validate_paths(
            self.modifiable_files,
            label="modifiable file",
        )
        frozen = _validate_paths(self.frozen_files, label="frozen file")
        overlap = modifiable & frozen
        if overlap:
            raise ValueError(
                "modifiable and frozen paths overlap: "
                + ", ".join(sorted(overlap))
            )

        command_ids: set[str] = set()
        for command in self.commands:
            command.validate()
            if command.command_id in command_ids:
                raise ValueError(
                    f"duplicate command_id: {command.command_id}"
                )
            command_ids.add(command.command_id)

        artifact_keys: set[tuple[str, str]] = set()
        for artifact in self.expected_artifacts:
            artifact.validate()
            key = (artifact.producer_step, artifact.artifact_type.value)
            if key in artifact_keys:
                raise ValueError(
                    "duplicate expected artifact: "
                    f"{artifact.producer_step}/{artifact.artifact_type.value}"
                )
            artifact_keys.add(key)

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        payload = asdict(self)
        for item in payload["expected_artifacts"]:
            artifact_type = item["artifact_type"]
            item["artifact_type"] = (
                artifact_type.value
                if isinstance(artifact_type, ResearchArtifactType)
                else str(artifact_type)
            )
        return payload

    def to_json(self) -> str:
        return json.dumps(
            self.to_dict(),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )

    def digest(self) -> str:
        return sha256(self.to_json().encode("utf-8")).hexdigest()

    @classmethod
    def from_json(cls, raw: str) -> "EpisodePackage":
        payload = json.loads(raw)
        package = cls(
            episode_id=str(payload["episode_id"]),
            run_id=str(payload["run_id"]),
            task_type=str(payload["task_type"]),
            repository=str(payload["repository"]),
            goal=str(payload["goal"]),
            base_revision=str(payload["base_revision"]),
            acceptance_criteria=tuple(payload["acceptance_criteria"]),
            modifiable_files=tuple(payload["modifiable_files"]),
            commands=tuple(
                EpisodeCommand(
                    command_id=str(item["command_id"]),
                    stage=str(item["stage"]),
                    argv=tuple(str(value) for value in item["argv"]),
                    cwd=str(item.get("cwd", ".")),
                    timeout_seconds=float(item.get("timeout_seconds", 300.0)),
                    required=bool(item.get("required", True)),
                )
                for item in payload.get("commands", ())
            ),
            expected_artifacts=tuple(
                EpisodeExpectedArtifact(
                    artifact_type=ResearchArtifactType(item["artifact_type"]),
                    producer_step=str(item["producer_step"]),
                    required=bool(item.get("required", True)),
                    verifier=str(item.get("verifier", "host")),
                )
                for item in payload.get("expected_artifacts", ())
            ),
            issue_number=(
                int(payload["issue_number"])
                if payload.get("issue_number") is not None
                else None
            ),
            frozen_files=tuple(payload.get("frozen_files", ())),
            environment={
                str(key): str(value)
                for key, value in dict(payload.get("environment") or {}).items()
            },
            metadata=dict(payload.get("metadata") or {}),
            schema_version=int(payload.get("schema_version", 1)),
        )
        package.validate()
        return package


def _validate_relative_path(
    raw: str,
    *,
    label: str,
    allow_dot: bool = False,
) -> str:
    value = str(raw).replace("\\", "/").strip()
    if allow_dot and value == ".":
        return value
    if not value:
        raise ValueError(f"{label} cannot be blank")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"{label} must be repository-relative: {raw!r}")
    if any(character in value for character in "*?[]{}"):
        raise ValueError(f"{label} cannot contain wildcards: {raw!r}")
    normalized = path.as_posix()
    if normalized in {"", "."}:
        raise ValueError(f"{label} must name a file or directory")
    return normalized


def _validate_paths(values: tuple[str, ...], *, label: str) -> set[str]:
    normalized = {
        _validate_relative_path(value, label=label)
        for value in values
    }
    if len(normalized) != len(values):
        raise ValueError(f"duplicate {label} path")
    return normalized
