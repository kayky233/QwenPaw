"""GitHub push and pull-request delivery for supervised AutoResearch."""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import tempfile
from pathlib import Path
from types import ModuleType
from typing import Any, Awaitable, Callable, Mapping
from urllib.error import HTTPError, URLError
from urllib.request import Request as UrlRequest, urlopen

from .research_worktree_service import (
    resolve_remote_default_branch,
    validate_git_branch_name,
)

RunProcess = Callable[..., Awaitable[str]]
RestCreator = Callable[[str, dict[str, str], str], str]

_ISSUE_NUMBER_RE = re.compile(r"(?:issues?/|#)(\d{1,10})", re.IGNORECASE)
_GITHUB_REPOSITORY_NAME_RE = re.compile(
    r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$",
)


def validate_github_repository_name(repository: str) -> str:
    """Validate an ``owner/repository`` name before API or CLI use."""

    value = repository.strip()
    if not _GITHUB_REPOSITORY_NAME_RE.fullmatch(value):
        raise RuntimeError(f"Invalid GitHub repository: {repository!r}")
    return value


def dialog_pr_head(dialog: Any) -> str:
    """Return the correct PR head for same-repository and fork workflows."""

    upstream = validate_github_repository_name(dialog.upstream_repository)
    push = validate_github_repository_name(dialog.push_repository)
    branch = validate_git_branch_name(dialog.branch)
    push_owner = push.split("/", 1)[0]
    upstream_owner = upstream.split("/", 1)[0]
    return branch if push_owner == upstream_owner else f"{push_owner}:{branch}"


def dialog_pr_title(dialog: Any) -> str:
    """Build the current issue-oriented PR title without trusting raw markup."""

    issue_match = _ISSUE_NUMBER_RE.search(
        f"{dialog.plan_markdown or ''}\n{dialog.goal}",
    )
    if issue_match:
        return f"fix: resolve issue #{issue_match.group(1)}"
    goal = " ".join(dialog.goal.strip().split())[:180]
    if not goal:
        raise RuntimeError("Cannot create a pull request with an empty goal")
    return f"fix: {goal}"


def build_pull_request_payload(
    dialog: Any,
    *,
    base_branch: str,
    body: str,
) -> dict[str, str]:
    """Build a validated GitHub pull-request request payload."""

    return {
        "title": dialog_pr_title(dialog),
        "head": dialog_pr_head(dialog),
        "base": validate_git_branch_name(base_branch),
        "body": body,
    }


async def resolve_delivery_base_branch(
    runtime_context: dict[str, Any],
    *,
    worktree: Path,
    run_process: RunProcess,
) -> str:
    """Resolve and cache the exact base branch used by the worktree."""

    cached = runtime_context.get("base_branch")
    if isinstance(cached, str) and cached.strip():
        return validate_git_branch_name(cached)
    branch = await resolve_remote_default_branch(run_process, worktree)
    runtime_context["base_branch"] = branch
    return branch


async def push_research_branch(
    worktree: Path,
    branch: str,
    *,
    run_process: RunProcess,
) -> None:
    """Push the validated research branch to its configured origin."""

    safe_branch = validate_git_branch_name(branch)
    await run_process(
        ["git", "push", "-u", "origin", safe_branch],
        cwd=worktree,
        timeout=600,
    )


async def create_dialog_pr(
    worktree: Path,
    dialog: Any,
    *,
    runtime_context: dict[str, Any],
    run_process: RunProcess,
    build_body: Callable[[Any], str],
    executable_lookup: Callable[[str], str | None] = shutil.which,
    environment: Mapping[str, str] | None = None,
    rest_creator: RestCreator | None = None,
) -> str:
    """Create a GitHub PR using CLI first and REST as a credentialed fallback."""

    validate_github_repository_name(dialog.upstream_repository)
    validate_github_repository_name(dialog.push_repository)
    base_branch = await resolve_delivery_base_branch(
        runtime_context,
        worktree=worktree,
        run_process=run_process,
    )
    body = build_body(dialog)
    payload = build_pull_request_payload(
        dialog,
        base_branch=base_branch,
        body=body,
    )

    if executable_lookup("gh") is not None:
        with tempfile.TemporaryDirectory(prefix="qwenpaw-research-pr-") as temp:
            body_file = Path(temp) / "pull-request.md"
            body_file.write_text(body, encoding="utf-8")
            pr_url = await run_process(
                [
                    "gh",
                    "pr",
                    "create",
                    "--repo",
                    dialog.upstream_repository,
                    "--base",
                    base_branch,
                    "--head",
                    payload["head"],
                    "--title",
                    payload["title"],
                    "--body-file",
                    str(body_file),
                ],
                cwd=worktree,
                timeout=120,
            )
        if not pr_url.strip():
            raise RuntimeError("GitHub CLI did not return a pull request URL")
        return pr_url.strip()

    env = environment if environment is not None else os.environ
    token = env.get("GITHUB_TOKEN") or env.get("GH_TOKEN")
    if not token:
        raise RuntimeError(
            "Cannot create the research PR: install and authenticate GitHub "
            "CLI, or set GITHUB_TOKEN or GH_TOKEN with pull request write "
            "permission",
        )
    creator = rest_creator or create_dialog_pr_via_rest
    return await asyncio.to_thread(
        creator,
        dialog.upstream_repository,
        payload,
        token,
    )


def create_dialog_pr_via_rest(
    repository: str,
    payload: dict[str, str],
    token: str,
) -> str:
    """Create a pull request through GitHub REST without leaking the token."""

    safe_repository = validate_github_repository_name(repository)
    request = UrlRequest(
        f"https://api.github.com/repos/{safe_repository}/pulls",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "QwenPaw-AutoResearch",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=120) as response:
            status = response.status
            raw_response = response.read()
    except HTTPError as exc:
        raise RuntimeError(
            "GitHub API PR creation failed with "
            f"HTTP {exc.code} ({exc.reason})",
        ) from None
    except URLError as exc:
        raise RuntimeError(
            f"GitHub API PR creation failed: {exc.reason}",
        ) from None
    if status != 201:
        raise RuntimeError(
            f"GitHub API PR creation failed with HTTP {status}",
        )
    try:
        response_data = json.loads(raw_response)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise RuntimeError(
            "GitHub API returned an invalid PR creation response",
        ) from exc
    pr_url = response_data.get("html_url")
    if not isinstance(pr_url, str) or not pr_url.strip():
        raise RuntimeError("GitHub API did not return a pull request URL")
    return pr_url.strip()


def install_research_delivery_service(research_module: ModuleType) -> None:
    """Install delivery functions while preserving existing router call sites."""

    async def push(worktree: Path, branch: str) -> None:
        await push_research_branch(
            worktree,
            branch,
            run_process=research_module._run_process,
        )

    async def create_pr(worktree: Path, dialog: Any) -> str:
        runtime_context = research_module._dialog_runtime_context.setdefault(
            dialog.plan_id,
            {},
        )
        return await create_dialog_pr(
            worktree,
            dialog,
            runtime_context=runtime_context,
            run_process=research_module._run_process,
            build_body=research_module._build_dialog_pr_body,
        )

    research_module._validate_github_repository_name = (
        validate_github_repository_name
    )
    research_module._dialog_pr_head = dialog_pr_head
    research_module._dialog_pr_title = dialog_pr_title
    research_module._build_pull_request_payload = build_pull_request_payload
    research_module._resolve_delivery_base_branch = resolve_delivery_base_branch
    research_module._push_research_branch = push
    research_module._create_dialog_pr = create_pr
    research_module._create_dialog_pr_via_rest = create_dialog_pr_via_rest
