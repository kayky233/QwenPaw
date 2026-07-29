"""Exclusive file leases for concurrent AutoResearch agents."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath


class FileLeaseConflict(RuntimeError):
    pass


@dataclass(frozen=True)
class FileLease:
    path: str
    owner: str
    run_id: str
    step_id: str


def normalize_repository_path(path: str) -> str:
    normalized = path.replace("\\", "/").strip()
    candidate = PurePosixPath(normalized)
    if (
        not normalized
        or candidate.is_absolute()
        or ".." in candidate.parts
        or normalized.endswith("/")
    ):
        raise ValueError(f"unsafe repository file path: {path!r}")
    return candidate.as_posix()


class FileLeaseRegistry:
    def __init__(self) -> None:
        self._leases: dict[str, FileLease] = {}

    def acquire(
        self,
        paths: tuple[str, ...],
        *,
        owner: str,
        run_id: str,
        step_id: str,
    ) -> tuple[FileLease, ...]:
        normalized = tuple(normalize_repository_path(path) for path in paths)
        conflicts = [
            self._leases[path]
            for path in normalized
            if path in self._leases and self._leases[path].owner != owner
        ]
        if conflicts:
            detail = ", ".join(
                f"{item.path} held by {item.owner}" for item in conflicts
            )
            raise FileLeaseConflict(detail)

        leases = tuple(
            FileLease(path, owner, run_id, step_id) for path in normalized
        )
        for lease in leases:
            self._leases[lease.path] = lease
        return leases

    def release_owner(self, owner: str, *, run_id: str | None = None) -> None:
        removable = [
            path
            for path, lease in self._leases.items()
            if lease.owner == owner and (run_id is None or lease.run_id == run_id)
        ]
        for path in removable:
            self._leases.pop(path, None)

    def list_for_run(self, run_id: str) -> tuple[FileLease, ...]:
        return tuple(
            lease for lease in self._leases.values() if lease.run_id == run_id
        )
