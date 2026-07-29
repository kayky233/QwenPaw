"""Git worktree preparation for supervised AutoResearch repository tasks."""

from __future__ import annotations

import re
from pathlib import Path
from types import ModuleType
from typing import Any, Awaitable, Callable

RunProcess = Callable[..., Awaitable[str]]
GithubRepositoryResolver = Callable[[Any], tuple[str, str, str]]
GithubRemoteResolver = Callable[[str], tuple[str, str] | None]

_ISSUE_NUMBER_RE = re.compile(r"(?:issues?/|#)(\d{1,10})", re.IGNORECASE)
_SAFE_BRANCH_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,199}$")


def validate_git_branch_name(branch: str) -> str:
    """Validate a branch name before interpolating it into Git commands."""

    value = branch.strip()
    invalid = (
        not _SAFE_BRANCH_RE.fullmatch(value)
        or value.startswith("-")
        or ".." in value
        or "@{" in value
        or "//" in value
        or value.endswith(("/", ".", ".lock"))
    )
    if invalid:
        raise RuntimeError(f"Invalid Git branch name: {branch!r}")
    return value


def parse_remote_head_symbolic_ref(output: str, *, remote: str = "origin") -> str:
    """Parse ``git symbolic-ref --short refs/remotes/<remote>/HEAD`` output."""

    value = output.strip()
    prefixes = (f"refs/remotes/{remote}/", f"{remote}/")
    for prefix in prefixes:
        if value.startswith(prefix):
            return validate_git_branch_name(value[len(prefix) :])
    raise RuntimeError(
        f"Git remote HEAD did not reference {remote!r}: {output!r}",
    )


def parse_ls_remote_default_branch(output: str) -> str:
    """Parse the symbolic HEAD line returned by ``git ls-remote --symref``."""

    for line in output.splitlines():
        match = re.fullmatch(r"ref:\s+refs/heads/([^\s]+)\s+HEAD", line.strip())
        if match:
            return validate_git_branch_name(match.group(1))
    raise RuntimeError("Git remote did not advertise a symbolic default branch")


def select_remote_branch(output: str, *, remote: str = "origin") -> str:
    """Select a safe fallback branch from remote-tracking refs."""

    candidates: list[str] = []
    for raw_line in output.splitlines():
        value = raw_line.strip()
        if not value or value == f"{remote}/HEAD":
            continue
        try:
            candidates.append(parse_remote_head_symbolic_ref(value, remote=remote))
        except RuntimeError:
            continue
    for preferred in ("main", "master"):
        if preferred in candidates:
            return preferred
    if candidates:
        return sorted(set(candidates))[0]
    raise RuntimeError("Unable to determine a safe remote default branch")


async def resolve_remote_default_branch(
    run_process: RunProcess,
    source_root: Path,
    *,
    remote: str = "origin",
) -> str:
    """Resolve the remote default branch without assuming ``main``."""

    try:
        symbolic = await run_process(
            [
                "git",
                "symbolic-ref",
                "--quiet",
                "--short",
                f"refs/remotes/{remote}/HEAD",
            ],
            cwd=source_root,
            timeout=30,
        )
        return parse_remote_head_symbolic_ref(symbolic, remote=remote)
    except RuntimeError:
        pass

    try:
        advertised = await run_process(
            ["git", "ls-remote", "--symref", remote, "HEAD"],
            cwd=source_root,
            timeout=120,
        )
        return parse_ls_remote_default_branch(advertised)
    except RuntimeError:
        pass

    refs = await run_process(
        [
            "git",
            "for-each-ref",
            "--format=%(refname:short)",
            f"refs/remotes/{remote}",
        ],
        cwd=source_root,
        timeout=30,
    )
    return select_remote_branch(refs, remote=remote)


def build_research_branch(dialog: Any) -> str:
    """Build a deterministic, bounded branch name for a dialog plan."""

    text = f"{dialog.plan_markdown or ''}\n{dialog.goal}"
    issue_match = _ISSUE_NUMBER_RE.search(text)
    issue = issue_match.group(1) if issue_match else "task"
    suffix = re.sub(r"[^a-z0-9]+", "", dialog.plan_id.lower())[:8] or "run"
    return validate_git_branch_name(f"autoresearch/issue-{issue}-{suffix}")


def reusable_worktree(dialog: Any) -> Path | None:
    """Return a safely reusable preserved worktree, or ``None`` if absent."""

    if not dialog.worktree_path:
        return None
    worktree = Path(dialog.worktree_path)
    if (
        worktree.is_dir()
        and (worktree / ".git").exists()
        and dialog.branch
        and dialog.upstream_repository
        and dialog.push_repository
    ):
        validate_git_branch_name(dialog.branch)
        return worktree
    raise RuntimeError(
        "Existing research worktree is unavailable; preserved progress "
        "cannot be resumed safely",
    )


async def prepare_research_worktree(
    dialog: Any,
    *,
    package_root: Path,
    working_dir: Path,
    runtime_context: dict[str, Any],
    run_process: RunProcess,
    github_repository: GithubRepositoryResolver,
    github_remote_identity: GithubRemoteResolver,
) -> tuple[Path, str, str, str]:
    """Prepare or resume the isolated worktree used by a repository task."""

    preserved = reusable_worktree(dialog)
    if preserved is not None:
        return (
            preserved,
            dialog.branch,
            dialog.upstream_repository,
            dialog.push_repository,
        )

    owner, repository, clone_url = github_repository(dialog)
    upstream_repository = f"{owner}/{repository}"
    source_root: Path | None = None
    push_repository = upstream_repository

    if (package_root / ".git").exists():
        remote_url = await run_process(
            ["git", "remote", "get-url", "origin"],
            cwd=package_root,
            timeout=30,
        )
        remote_identity = github_remote_identity(remote_url)
        if (
            remote_identity is not None
            and remote_identity[1].casefold() == repository.casefold()
        ):
            source_root = package_root
            push_repository = f"{remote_identity[0]}/{remote_identity[1]}"

    if source_root is None:
        workspace = runtime_context.get("workspace")
        workspace_dir = Path(
            getattr(workspace, "workspace_dir", working_dir),
        ).expanduser()
        source_root = (
            workspace_dir
            / ".qwenpaw"
            / "research-repositories"
            / f"{owner}-{repository}"
        )
        if not (source_root / ".git").exists():
            source_root.parent.mkdir(parents=True, exist_ok=True)
            await run_process(
                ["git", "clone", clone_url, str(source_root)],
                cwd=source_root.parent,
                timeout=600,
            )

    base_branch = await resolve_remote_default_branch(run_process, source_root)
    await run_process(
        ["git", "fetch", "origin", base_branch],
        cwd=source_root,
        timeout=600,
    )

    branch = build_research_branch(dialog)
    suffix = branch.rsplit("-", 1)[-1]
    worktree = source_root / ".qwenpaw" / "worktrees" / f"research-{suffix}"
    worktree.parent.mkdir(parents=True, exist_ok=True)
    await run_process(
        [
            "git",
            "worktree",
            "add",
            str(worktree),
            "-b",
            branch,
            f"origin/{base_branch}",
        ],
        cwd=source_root,
        timeout=120,
    )

    runtime_context["base_branch"] = base_branch
    runtime_context["source_root"] = str(source_root)
    return worktree, branch, upstream_repository, push_repository


def install_research_worktree_service(research_module: ModuleType) -> None:
    """Install the extracted worktree service on the legacy router surface."""

    async def prepare(dialog: Any) -> tuple[Path, str, str, str]:
        runtime_context = research_module._dialog_runtime_context.setdefault(
            dialog.plan_id,
            {},
        )
        return await prepare_research_worktree(
            dialog,
            package_root=Path(research_module.__file__).resolve().parents[4],
            working_dir=research_module.WORKING_DIR,
            runtime_context=runtime_context,
            run_process=research_module._run_process,
            github_repository=research_module._github_repository,
            github_remote_identity=research_module._github_remote_identity,
        )

    research_module._validate_git_branch_name = validate_git_branch_name
    research_module._resolve_remote_default_branch = resolve_remote_default_branch
    research_module._build_research_branch = build_research_branch
    research_module._reusable_worktree = reusable_worktree
    research_module._prepare_research_worktree = prepare
