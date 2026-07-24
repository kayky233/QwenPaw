"""LocalArtifactStore — Protocol and file-based implementation for storing
research artifacts (source code dumps, metrics JSON, logs) on local disk.

Uses typing.Protocol for the contract (consistent with existing project patterns)
and a file-system implementation that stores artifacts under a root directory
with run-scoped subdirectories.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Protocol, runtime_checkable


@dataclass(frozen=True)
class ArtifactMetadata:
    """Metadata for a stored artifact."""

    run_id: str
    local_path: str
    content_hash: str
    size_bytes: int
    content_type: str
    created_at: str
    outcome_id: str | None = None


@runtime_checkable
class LocalArtifactStore(Protocol):
    """Protocol for local artifact storage.

    Implementations store artifact files on local disk and return
    metadata suitable for persisting in the research_artifacts table.
    """

    async def store(
        self,
        run_id: str,
        content: str | bytes,
        filename: str,
        content_type: str = "application/octet-stream",
        outcome_id: str | None = None,
    ) -> ArtifactMetadata:
        """Store artifact content and return metadata."""
        ...

    async def retrieve(self, run_id: str, filename: str) -> bytes:
        """Read artifact content from disk."""
        ...

    async def retrieve_text(self, run_id: str, filename: str) -> str:
        """Read artifact content as UTF-8 text."""
        ...

    async def delete_run_artifacts(self, run_id: str) -> int:
        """Delete all artifacts for a run. Returns count of files removed."""
        ...

    async def run_artifact_paths(self, run_id: str) -> list[str]:
        """List relative paths of all artifacts for a run."""
        ...


class FileLocalArtifactStore:
    """File-system implementation of LocalArtifactStore.

    Stores artifacts in: <root>/<run_id>/<filename>
    Uses atomic writes (write-temp → rename) for safety.
    """

    def __init__(self, root: Path | str):
        self._root = Path(root)
        self._root.mkdir(parents=True, exist_ok=True)

    def _run_dir(self, run_id: str) -> Path:
        return self._root / run_id

    @staticmethod
    def _sha256(content: str | bytes) -> str:
        if isinstance(content, str):
            content = content.encode("utf-8")
        return hashlib.sha256(content).hexdigest()

    async def store(
        self,
        run_id: str,
        content: str | bytes,
        filename: str,
        content_type: str = "application/octet-stream",
        outcome_id: str | None = None,
    ) -> ArtifactMetadata:
        run_dir = self._run_dir(run_id)
        run_dir.mkdir(parents=True, exist_ok=True)

        if isinstance(content, str):
            content_bytes = content.encode("utf-8")
        else:
            content_bytes = content

        content_hash = self._sha256(content_bytes)
        target_path = run_dir / filename

        # Ensure parent directory exists (filename may include subdirectories)
        target_path.parent.mkdir(parents=True, exist_ok=True)

        # Atomic write: temp file → rename
        tmp_path = target_path.with_suffix(target_path.suffix + ".tmp")
        tmp_path.write_bytes(content_bytes)
        tmp_path.rename(target_path)

        return ArtifactMetadata(
            run_id=run_id,
            local_path=str(target_path),
            content_hash=content_hash,
            size_bytes=len(content_bytes),
            content_type=content_type,
            created_at=datetime.now(timezone.utc).isoformat(),
            outcome_id=outcome_id,
        )

    async def retrieve(self, run_id: str, filename: str) -> bytes:
        target_path = self._run_dir(run_id) / filename
        if not target_path.exists():
            raise FileNotFoundError(
                f"Artifact not found: {target_path}"
            )
        return target_path.read_bytes()

    async def retrieve_text(self, run_id: str, filename: str) -> str:
        content = await self.retrieve(run_id, filename)
        return content.decode("utf-8")

    async def delete_run_artifacts(self, run_id: str) -> int:
        run_dir = self._run_dir(run_id)
        if not run_dir.exists():
            return 0
        count = sum(1 for _ in run_dir.iterdir() if _.is_file())
        for f in run_dir.iterdir():
            f.unlink()
        run_dir.rmdir()
        return count

    async def run_artifact_paths(self, run_id: str) -> list[str]:
        run_dir = self._run_dir(run_id)
        if not run_dir.exists():
            return []
        return sorted(
            str(f.relative_to(self._root)) for f in run_dir.rglob("*") if f.is_file()
        )
