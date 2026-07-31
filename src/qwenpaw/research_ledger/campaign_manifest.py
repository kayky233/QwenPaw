"""User-facing manifest for directly running an Issue Campaign."""

from __future__ import annotations

import json
import re
import shlex
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

_ISSUE_URL_RE = re.compile(
    r"^https://github\.com/"
    r"(?P<owner>[A-Za-z0-9_.-]+)/"
    r"(?P<repo>[A-Za-z0-9_.-]+)/issues/(?P<number>[1-9][0-9]*)/?$"
)
_TASK_TYPES = {"bug_fix", "feature", "refactor", "performance", "research"}
_DELIVERY_MODES = {"local", "draft_pr"}


@dataclass(frozen=True)
class CampaignManifestCommand:
    command_id: str
    stage: str
    argv: tuple[str, ...]
    cwd: str = "."
    timeout_seconds: float = 300.0
    required: bool = True

    @classmethod
    def from_shell(
        cls,
        raw: str,
        *,
        index: int,
        stage: str = "unit",
    ) -> "CampaignManifestCommand":
        argv = tuple(shlex.split(raw))
        if not argv:
            raise ValueError("validation command cannot be empty")
        return cls(
            command_id=f"check-{index}",
            stage=stage,
            argv=argv,
        )

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "CampaignManifestCommand":
        return cls(
            command_id=str(raw.get("command_id", "")),
            stage=str(raw.get("stage", "unit")),
            argv=tuple(str(item) for item in raw.get("argv", ())),
            cwd=str(raw.get("cwd", ".")),
            timeout_seconds=float(raw.get("timeout_seconds", 300.0)),
            required=bool(raw.get("required", True)),
        )

    def validate(self) -> None:
        if not self.command_id.strip():
            raise ValueError("command_id is required")
        if not self.stage.strip():
            raise ValueError("command stage is required")
        if not self.argv or not all(item.strip() for item in self.argv):
            raise ValueError(f"command {self.command_id!r} requires argv")
        if self.cwd.startswith("/") or ".." in Path(self.cwd).parts:
            raise ValueError("command cwd must remain repository-relative")
        if self.timeout_seconds <= 0 or self.timeout_seconds > 3600:
            raise ValueError("command timeout must be between 0 and 3600 seconds")

    def to_api_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            "command_id": self.command_id,
            "stage": self.stage,
            "argv": list(self.argv),
            "cwd": self.cwd,
            "timeout_seconds": self.timeout_seconds,
            "required": self.required,
        }


@dataclass(frozen=True)
class CampaignManifest:
    issue_url: str
    task_type: str = "bug_fix"
    acceptance_criteria: tuple[str, ...] = (
        "Resolve the linked issue without introducing regressions.",
    )
    modifiable_files: tuple[str, ...] = ()
    frozen_files: tuple[str, ...] = ()
    commands: tuple[CampaignManifestCommand, ...] = ()
    implementer_agent_id: str = "implementer"
    reviewer_agent_id: str = "reviewer"
    max_attempts: int = 3
    delivery_mode: str = "local"
    session_id: str | None = None
    schema_version: int = 1
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def repository(self) -> str:
        match = _ISSUE_URL_RE.fullmatch(self.issue_url.strip())
        if match is None:
            raise ValueError("issue_url must be a canonical GitHub Issue URL")
        return f"{match.group('owner')}/{match.group('repo')}"

    @property
    def issue_number(self) -> int:
        match = _ISSUE_URL_RE.fullmatch(self.issue_url.strip())
        if match is None:
            raise ValueError("issue_url must be a canonical GitHub Issue URL")
        return int(match.group("number"))

    def validate(self, *, ready: bool = True) -> None:
        self.repository
        if self.schema_version != 1:
            raise ValueError(
                f"unsupported Campaign manifest schema: {self.schema_version}"
            )
        if self.task_type not in _TASK_TYPES:
            raise ValueError(f"unsupported task_type: {self.task_type!r}")
        if self.delivery_mode not in _DELIVERY_MODES:
            raise ValueError(
                f"unsupported delivery_mode: {self.delivery_mode!r}"
            )
        if not self.implementer_agent_id.strip():
            raise ValueError("implementer_agent_id is required")
        if not self.reviewer_agent_id.strip():
            raise ValueError("reviewer_agent_id is required")
        if self.implementer_agent_id == self.reviewer_agent_id:
            raise ValueError("implementer and reviewer agents must be different")
        if self.max_attempts < 1 or self.max_attempts > 10:
            raise ValueError("max_attempts must be between 1 and 10")
        if not self.acceptance_criteria or not all(
            item.strip() for item in self.acceptance_criteria
        ):
            raise ValueError("at least one acceptance criterion is required")
        if len(set(self.modifiable_files)) != len(self.modifiable_files):
            raise ValueError("modifiable_files contains duplicates")
        if set(self.modifiable_files) & set(self.frozen_files):
            raise ValueError("modifiable_files and frozen_files overlap")
        for path in (*self.modifiable_files, *self.frozen_files):
            normalized = Path(path)
            if normalized.is_absolute() or ".." in normalized.parts:
                raise ValueError("Campaign paths must be repository-relative")
        for command in self.commands:
            command.validate()
        if ready and not self.modifiable_files:
            raise ValueError(
                "at least one modifiable file is required; approve scope explicitly"
            )
        if ready and not self.commands:
            raise ValueError("at least one validation command is required")

    def to_api_payload(self) -> dict[str, Any]:
        self.validate(ready=True)
        return {
            "repository": self.repository,
            "issue_number": self.issue_number,
            "task_type": self.task_type,
            "acceptance_criteria": list(self.acceptance_criteria),
            "modifiable_files": list(self.modifiable_files),
            "frozen_files": list(self.frozen_files),
            "commands": [command.to_api_dict() for command in self.commands],
            "implementer_agent_id": self.implementer_agent_id,
            "reviewer_agent_id": self.reviewer_agent_id,
            "max_attempts": self.max_attempts,
            "delivery_mode": self.delivery_mode,
            "session_id": self.session_id,
        }

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["commands"] = [command.to_api_dict() for command in self.commands]
        return payload

    def write(self, path: Path) -> Path:
        self.validate(ready=False)
        target = path.expanduser().resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(
                self.to_dict(),
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        return target

    @classmethod
    def read(cls, path: Path) -> "CampaignManifest":
        payload = json.loads(
            path.expanduser().resolve().read_text(encoding="utf-8")
        )
        manifest = cls(
            issue_url=str(payload.get("issue_url", "")),
            task_type=str(payload.get("task_type", "bug_fix")),
            acceptance_criteria=tuple(
                str(item) for item in payload.get("acceptance_criteria", ())
            ),
            modifiable_files=tuple(
                str(item) for item in payload.get("modifiable_files", ())
            ),
            frozen_files=tuple(
                str(item) for item in payload.get("frozen_files", ())
            ),
            commands=tuple(
                CampaignManifestCommand.from_dict(item)
                for item in payload.get("commands", ())
                if isinstance(item, dict)
            ),
            implementer_agent_id=str(
                payload.get("implementer_agent_id", "implementer")
            ),
            reviewer_agent_id=str(
                payload.get("reviewer_agent_id", "reviewer")
            ),
            max_attempts=int(payload.get("max_attempts", 3)),
            delivery_mode=str(payload.get("delivery_mode", "local")),
            session_id=(
                str(payload["session_id"])
                if payload.get("session_id") is not None
                else None
            ),
            schema_version=int(payload.get("schema_version", 1)),
            metadata=dict(payload.get("metadata") or {}),
        )
        manifest.validate(ready=False)
        return manifest
