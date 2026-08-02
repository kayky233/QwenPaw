"""Safe recovery export and cleanup for durable Campaign worktrees."""

from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from types import ModuleType
from typing import Any

from fastapi import HTTPException, Request
from pydantic import BaseModel, Field

WORKTREE_CLEANUP_CONFIRMATION = "CLEANUP_AUTORESEARCH_CAMPAIGN_WORKTREE"
_ALLOWED_CLEANUP_STATUSES = {
    "delivered",
    "needs_revision",
    "blocked",
    "failed",
    "cancelled",
}


class CleanupCampaignWorktreeRequest(BaseModel):
    confirmation: str = Field(min_length=1, max_length=100)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _persist(research_module: ModuleType, state: Any) -> None:
    target = Path(research_module._campaign_snapshot_root) / f"{state.campaign_id}.json"
    _atomic_json(target, asdict(state))


def _owned(research_module: ModuleType, campaign_id: str, request: Request) -> Any:
    state = research_module._campaign_runs.get(campaign_id)
    if state is None:
        raise HTTPException(status_code=404, detail="Unknown campaign")
    current_agent, current_user, _ = research_module._request_owner_identity(request)
    if state.owner_agent_id != current_agent or state.owner_user_id != current_user:
        raise HTTPException(status_code=404, detail="Unknown campaign")
    return state


def _parse_worktree_paths(raw: str) -> tuple[Path, ...]:
    return tuple(
        Path(line[len("worktree ") :].strip()).expanduser().resolve()
        for line in raw.splitlines()
        if line.startswith("worktree ") and line[len("worktree ") :].strip()
    )


async def _source_root(research_module: ModuleType, worktree: Path) -> Path:
    common_raw = (
        await research_module._run_process(
            ["git", "rev-parse", "--git-common-dir"],
            cwd=worktree,
            timeout=30,
        )
    ).strip()
    common = Path(common_raw)
    common = (
        (worktree / common).resolve()
        if not common.is_absolute()
        else common.resolve()
    )
    if common.name != ".git" or not common.is_dir():
        raise RuntimeError(
            "Campaign worktree does not belong to a normal Git repository"
        )
    source = common.parent.resolve()
    if not (source / ".git").is_dir():
        raise RuntimeError("Campaign source repository is unavailable")
    return source


async def _validate_identity(
    research_module: ModuleType,
    state: Any,
) -> tuple[Path, Path]:
    worktree = Path(state.worktree_path).expanduser().resolve()
    if not worktree.is_dir() or not (worktree / ".git").exists():
        raise RuntimeError("Campaign worktree is already absent or invalid")
    branch = str(state.branch)
    if not branch.startswith("autoresearch/issue-"):
        raise RuntimeError("refusing to clean a non-AutoResearch branch")
    current_branch = (
        await research_module._run_process(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=worktree,
            timeout=30,
        )
    ).strip()
    if current_branch != branch:
        raise RuntimeError("Campaign worktree branch identity changed")
    source = await _source_root(research_module, worktree)
    registered_raw = await research_module._run_process(
        ["git", "worktree", "list", "--porcelain"],
        cwd=source,
        timeout=30,
    )
    if worktree not in _parse_worktree_paths(registered_raw):
        raise RuntimeError(
            "Campaign worktree is not registered in its source repository"
        )
    try:
        worktree.relative_to(source)
    except ValueError as exc:
        raise RuntimeError(
            "Campaign worktree escapes its source repository; cleanup is blocked"
        ) from exc
    return worktree, source


def _safe_untracked_path(raw: str) -> PurePosixPath:
    normalized = raw.replace("\\", "/")
    path = PurePosixPath(normalized)
    if not normalized or path.is_absolute() or ".." in path.parts:
        raise RuntimeError(f"unsafe untracked recovery path: {raw!r}")
    return path


async def _backup_untracked_files(
    research_module: ModuleType,
    worktree: Path,
    root: Path,
) -> tuple[dict[str, Any], ...]:
    raw = await research_module._run_process(
        ["git", "ls-files", "--others", "--exclude-standard", "-z"],
        cwd=worktree,
        timeout=30,
    )
    backup_root = root / "untracked"
    records: list[dict[str, Any]] = []
    for value in (item for item in raw.split("\0") if item):
        relative = _safe_untracked_path(value)
        source = (worktree / Path(*relative.parts)).resolve()
        try:
            source.relative_to(worktree)
        except ValueError as exc:
            raise RuntimeError(
                f"untracked recovery path escapes worktree: {value!r}"
            ) from exc
        if source.is_symlink():
            records.append(
                {
                    "path": relative.as_posix(),
                    "status": "symlink_not_copied",
                }
            )
            continue
        if not source.is_file():
            records.append(
                {
                    "path": relative.as_posix(),
                    "status": "non_regular_not_copied",
                }
            )
            continue
        target = backup_root.joinpath(*relative.parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        digest = hashlib.sha256(target.read_bytes()).hexdigest()
        records.append(
            {
                "path": relative.as_posix(),
                "backup_path": str(target),
                "sha256": digest,
                "bytes": target.stat().st_size,
                "status": "copied",
            }
        )
    return tuple(records)


async def _export_recovery(
    research_module: ModuleType,
    state: Any,
    worktree: Path,
) -> dict[str, Any]:
    status = await research_module._run_process(
        ["git", "status", "--porcelain=v1", "-z"],
        cwd=worktree,
        timeout=30,
    )
    diff = await research_module._run_process(
        ["git", "diff", "--binary", "HEAD"],
        cwd=worktree,
        timeout=120,
    )
    root = (
        Path(research_module._campaign_snapshot_root)
        / "recovery"
        / state.campaign_id
    )
    root.mkdir(parents=True, exist_ok=True)
    patch_path = root / "uncommitted.patch"
    status_path = root / "status.txt"
    patch_path.write_text(diff, encoding="utf-8")
    status_path.write_text(status.replace("\0", "\n"), encoding="utf-8")
    untracked = await _backup_untracked_files(
        research_module,
        worktree,
        root,
    )
    manifest_path = root / "recovery.json"
    manifest = {
        "patch_path": str(patch_path),
        "patch_sha256": hashlib.sha256(diff.encode("utf-8")).hexdigest(),
        "patch_bytes": len(diff.encode("utf-8")),
        "status_path": str(status_path),
        "untracked": list(untracked),
        "had_uncommitted_changes": bool(status.strip()),
        "exported_at": _utc_now(),
    }
    _atomic_json(manifest_path, manifest)
    return {**manifest, "manifest_path": str(manifest_path)}


async def _cleanup_worktree(
    research_module: ModuleType,
    state: Any,
) -> dict[str, Any]:
    worktree, source = await _validate_identity(research_module, state)
    recovery = await _export_recovery(research_module, state, worktree)
    branch = str(state.branch)
    await research_module._run_process(
        ["git", "worktree", "remove", "--force", str(worktree)],
        cwd=source,
        timeout=120,
    )
    branches = await research_module._run_process(
        ["git", "branch", "--list", branch],
        cwd=source,
        timeout=30,
    )
    if branches.strip():
        await research_module._run_process(
            ["git", "branch", "-D", branch],
            cwd=source,
            timeout=30,
        )
    result = {
        "status": "cleaned",
        "worktree": str(worktree),
        "source_repository": str(source),
        "local_branch": branch,
        "remote_branch_deleted": False,
        "change_request_modified": False,
        "recovery": recovery,
        "cleaned_at": _utc_now(),
    }
    outcome = state.outcome if isinstance(state.outcome, dict) else {}
    outcome["worktree_cleanup"] = result
    state.outcome = outcome
    state.worktree_path = ""
    state.updated_at = result["cleaned_at"]
    state.events.append(
        {
            "phase": "worktree_cleaned",
            "detail": (
                "local worktree and branch removed; "
                f"recovery={recovery['manifest_path']}; "
                "remote PR and branch unchanged"
            ),
            "timestamp": state.updated_at,
            "sequence": len(state.events) + 1,
        }
    )
    _persist(research_module, state)
    return result


def install_research_campaign_worktree_control_service(
    research_module: ModuleType,
) -> None:
    """Register worktree recovery metadata and explicit local cleanup."""

    if getattr(research_module, "_campaign_worktree_control_installed", False):
        return

    @research_module.router.get("/campaigns/{campaign_id}/recovery")
    async def campaign_recovery(
        campaign_id: str,
        request: Request,
    ) -> dict[str, Any]:
        state = _owned(research_module, campaign_id, request)
        outcome = state.outcome if isinstance(state.outcome, dict) else {}
        cleanup = outcome.get("worktree_cleanup")
        return {
            "campaign_id": campaign_id,
            "status": state.status,
            "worktree_path": state.worktree_path,
            "branch": state.branch,
            "base_branch": state.base_branch,
            "error": state.error,
            "cleanup": cleanup if isinstance(cleanup, dict) else None,
            "can_revise": bool(state.worktree_path)
            and state.status
            in {"needs_revision", "failed", "blocked", "delivered"},
            "can_cleanup": bool(state.worktree_path)
            and state.status in _ALLOWED_CLEANUP_STATUSES,
        }

    @research_module.router.post("/campaigns/{campaign_id}/cleanup-worktree")
    async def cleanup_campaign_worktree(
        campaign_id: str,
        body: CleanupCampaignWorktreeRequest,
        request: Request,
    ) -> dict[str, Any]:
        state = _owned(research_module, campaign_id, request)
        if body.confirmation != WORKTREE_CLEANUP_CONFIRMATION:
            raise HTTPException(
                status_code=422,
                detail="Campaign worktree cleanup confirmation mismatch",
            )
        task = research_module._campaign_tasks.get(campaign_id)
        if task is not None and not task.done():
            raise HTTPException(status_code=409, detail="Campaign is still running")
        if state.status not in _ALLOWED_CLEANUP_STATUSES:
            raise HTTPException(
                status_code=409,
                detail=f"Campaign status cannot be cleaned: {state.status}",
            )
        if not state.worktree_path:
            raise HTTPException(status_code=409, detail="Campaign worktree is absent")
        try:
            result = await _cleanup_worktree(research_module, state)
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {
            "campaign_id": campaign_id,
            "campaign_status": state.status,
            **result,
        }

    research_module.CleanupCampaignWorktreeRequest = CleanupCampaignWorktreeRequest
    research_module._campaign_worktree_control_installed = True
