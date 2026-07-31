"""Safe application and cleanup controls for local Campaign delivery."""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

APPLY_CONFIRMATION = "APPLY_AUTORESEARCH_PATCH"
CLEANUP_LOCAL_CONFIRMATION = "CLEANUP_AUTORESEARCH_LOCAL"
_GITHUB_REMOTE_RE = re.compile(
    r"^(?:https://github\.com/|git@github\.com:|ssh://git@github\.com/)"
    r"(?P<owner>[A-Za-z0-9_.-]+)/(?P<repo>[A-Za-z0-9_.-]+?)(?:\.git)?/?$"
)


@dataclass(frozen=True)
class LocalApplyResult:
    repository: str
    campaign_id: str
    patch_path: str
    changed_paths: tuple[str, ...]
    staged: bool
    committed: bool = False


@dataclass(frozen=True)
class LocalCleanupResult:
    campaign_id: str
    worktree: str
    branch: str
    removed: bool


def _run(repository: Path, *argv: str) -> str:
    completed = subprocess.run(
        list(argv),
        cwd=repository,
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()
        raise RuntimeError(
            f"command failed ({completed.returncode}): {' '.join(argv)}\n{detail}"
        )
    return completed.stdout.strip()


def _delivery_mode(state: dict[str, Any]) -> str:
    outcome = state.get("outcome")
    if not isinstance(outcome, dict):
        return ""
    return str(outcome.get("delivery_mode") or "")


def _local_delivery(state: dict[str, Any]) -> dict[str, Any]:
    if str(state.get("status", "")) != "delivered":
        raise RuntimeError("Campaign has not reached delivered")
    if _delivery_mode(state) != "local":
        raise RuntimeError("Campaign is not a local-delivery run")
    outcome = state.get("outcome")
    delivery = outcome.get("delivery") if isinstance(outcome, dict) else None
    if not isinstance(delivery, dict) or not str(delivery.get("url", "")).startswith(
        "local://"
    ):
        raise RuntimeError("Campaign does not contain a local delivery receipt")
    return outcome


def _code_diff(outcome: dict[str, Any]) -> dict[str, Any]:
    artifacts = outcome.get("artifacts", ())
    candidates = [
        item
        for item in artifacts
        if isinstance(item, dict)
        and item.get("artifact_type") == "code_diff"
        and item.get("verified") is True
    ]
    if not candidates:
        raise RuntimeError("Campaign does not contain a verified code diff")
    return candidates[-1]


def _same_revision(expected: str, actual: str) -> bool:
    expected = expected.strip().casefold()
    actual = actual.strip().casefold()
    return bool(expected) and (
        expected == actual
        or (len(expected) >= 7 and actual.startswith(expected))
    )


def _origin_repository(repository: Path) -> str:
    remote = _run(repository, "git", "remote", "get-url", "origin")
    match = _GITHUB_REMOTE_RE.fullmatch(remote)
    if match is None:
        raise RuntimeError("target origin is not a supported github.com remote")
    return f"{match.group('owner')}/{match.group('repo')}"


def apply_local_campaign_patch(
    state: dict[str, Any],
    repository: Path,
    *,
    confirmation: str,
) -> LocalApplyResult:
    """Apply and stage one verified local Campaign patch without committing."""

    if confirmation != APPLY_CONFIRMATION:
        raise RuntimeError("local Campaign apply confirmation mismatch")
    outcome = _local_delivery(state)
    repository = repository.expanduser().resolve()
    if not (repository / ".git").exists():
        raise RuntimeError(f"target is not a Git repository: {repository}")
    if _run(repository, "git", "status", "--porcelain"):
        raise RuntimeError("target repository must be clean before apply")
    expected_repository = str(state.get("repository", ""))
    if _origin_repository(repository).casefold() != expected_repository.casefold():
        raise RuntimeError("target origin does not match the Campaign repository")

    artifact = _code_diff(outcome)
    metadata = artifact.get("metadata")
    if not isinstance(metadata, dict):
        raise RuntimeError("code diff metadata is missing")
    parent_revision = str(metadata.get("parent_revision") or "")
    current_head = _run(repository, "git", "rev-parse", "HEAD")
    if not _same_revision(parent_revision, current_head):
        raise RuntimeError(
            "target HEAD does not match the verified Campaign parent revision"
        )
    patch_path = Path(str(artifact.get("path") or "")).expanduser().resolve()
    if not patch_path.is_file():
        raise RuntimeError(f"verified Campaign patch is missing: {patch_path}")
    changed_paths_raw = metadata.get("changed_paths")
    if not isinstance(changed_paths_raw, (list, tuple)) or not changed_paths_raw:
        raise RuntimeError("verified Campaign changed paths are missing")
    changed_paths = tuple(str(item) for item in changed_paths_raw)
    approved = {str(item) for item in state.get("modifiable_files", ())}
    violations = tuple(sorted(set(changed_paths) - approved))
    if violations:
        raise RuntimeError(
            "verified patch exceeds approved Campaign scope: "
            + ", ".join(violations)
        )

    _run(
        repository,
        "git",
        "apply",
        "--check",
        "--index",
        str(patch_path),
    )
    _run(repository, "git", "apply", "--index", str(patch_path))
    staged_paths = tuple(
        item
        for item in _run(
            repository,
            "git",
            "diff",
            "--cached",
            "--name-only",
        ).splitlines()
        if item
    )
    if staged_paths != changed_paths:
        raise RuntimeError(
            "staged paths do not match verified Campaign paths: "
            f"{staged_paths!r} != {changed_paths!r}"
        )
    return LocalApplyResult(
        repository=str(repository),
        campaign_id=str(state.get("campaign_id", "")),
        patch_path=str(patch_path),
        changed_paths=changed_paths,
        staged=True,
    )


def cleanup_local_campaign(
    state: dict[str, Any],
    *,
    confirmation: str,
) -> LocalCleanupResult:
    """Remove only the preserved local Campaign worktree and branch."""

    if confirmation != CLEANUP_LOCAL_CONFIRMATION:
        raise RuntimeError("local Campaign cleanup confirmation mismatch")
    _local_delivery(state)
    campaign_id = str(state.get("campaign_id", ""))
    worktree = Path(str(state.get("worktree_path") or "")).expanduser().resolve()
    branch = str(state.get("branch") or "")
    if not worktree.is_dir() or not (worktree / ".git").exists():
        raise RuntimeError("Campaign worktree is missing or already removed")
    if not branch.startswith("autoresearch/issue-"):
        raise RuntimeError("refusing to remove a non-Campaign branch")
    current_branch = _run(worktree, "git", "rev-parse", "--abbrev-ref", "HEAD")
    if current_branch != branch:
        raise RuntimeError("Campaign worktree branch identity changed")

    worktrees = _run(worktree, "git", "worktree", "list", "--porcelain")
    roots = [
        Path(line.removeprefix("worktree ")).resolve()
        for line in worktrees.splitlines()
        if line.startswith("worktree ")
    ]
    main = next((item for item in roots if item != worktree), None)
    if main is None:
        raise RuntimeError("unable to resolve the main Git worktree")
    _run(main, "git", "worktree", "remove", "--force", str(worktree))
    _run(main, "git", "branch", "-D", branch)
    return LocalCleanupResult(
        campaign_id=campaign_id,
        worktree=str(worktree),
        branch=branch,
        removed=True,
    )
