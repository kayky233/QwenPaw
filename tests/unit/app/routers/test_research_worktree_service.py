from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, call

import pytest

from qwenpaw.app.routers.research_github_service import (
    github_remote_identity,
    github_repository,
)
from qwenpaw.app.routers.research_worktree_service import (
    build_research_branch,
    install_research_worktree_service,
    parse_ls_remote_default_branch,
    parse_remote_head_symbolic_ref,
    prepare_research_worktree,
    read_local_remote_head,
    resolve_remote_default_branch,
    reusable_worktree,
    select_remote_branch,
    validate_git_branch_name,
)


def _dialog(**overrides):
    values = {
        "plan_id": "Plan-ABC_123",
        "goal": (
            "Fix "
            "https://github.com/agentscope-ai/QwenPaw/issues/42"
        ),
        "plan_markdown": "# Plan\n",
        "worktree_path": "",
        "branch": "",
        "upstream_repository": "",
        "push_repository": "",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _write_remote_head(
    repository_root: Path,
    branch: str,
) -> None:
    head = (
        repository_root
        / ".git"
        / "refs"
        / "remotes"
        / "origin"
        / "HEAD"
    )
    head.parent.mkdir(parents=True, exist_ok=True)
    head.write_text(
        f"ref: refs/remotes/origin/{branch}\n",
        encoding="utf-8",
    )


def test_parse_remote_head_accepts_short_full_and_ref_forms() -> None:
    assert parse_remote_head_symbolic_ref(
        "origin/develop\n",
    ) == "develop"
    assert parse_remote_head_symbolic_ref(
        "refs/remotes/origin/release/v2",
    ) == "release/v2"
    assert parse_remote_head_symbolic_ref(
        "ref: refs/remotes/origin/trunk",
    ) == "trunk"


def test_parse_ls_remote_default_branch_reads_symbolic_head() -> None:
    output = "ref: refs/heads/trunk\tHEAD\nabc123\tHEAD\n"

    assert parse_ls_remote_default_branch(output) == "trunk"


@pytest.mark.parametrize(
    "branch",
    [
        "-dangerous",
        "../main",
        "main..next",
        "main@{1}",
        "feature//double",
        "feature/",
        "feature.lock",
        "feature with space",
    ],
)
def test_validate_git_branch_name_rejects_unsafe_values(
    branch: str,
) -> None:
    with pytest.raises(RuntimeError, match="Invalid Git branch"):
        validate_git_branch_name(branch)


def test_read_local_remote_head_from_standard_git_directory(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    _write_remote_head(repository, "develop")

    assert read_local_remote_head(repository) == "develop"


def test_read_local_remote_head_from_worktree_gitdir_file(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "worktree"
    git_dir = tmp_path / "metadata" / "worktrees" / "one"
    head = git_dir / "refs" / "remotes" / "origin" / "HEAD"
    head.parent.mkdir(parents=True)
    head.write_text(
        "ref: refs/remotes/origin/release/v2\n",
        encoding="utf-8",
    )
    repository.mkdir()
    (repository / ".git").write_text(
        f"gitdir: {git_dir}\n",
        encoding="utf-8",
    )

    assert read_local_remote_head(repository) == "release/v2"


def test_read_local_remote_head_returns_none_for_invalid_metadata(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    _write_remote_head(repository, "../unsafe")

    assert read_local_remote_head(repository) is None
    assert read_local_remote_head(tmp_path / "missing") is None


def test_select_remote_branch_prefers_main_then_master() -> None:
    refs = (
        "origin/HEAD\n"
        "origin/release\n"
        "origin/master\n"
        "origin/main\n"
    )

    assert select_remote_branch(refs) == "main"


def test_select_remote_branch_uses_stable_fallback_order() -> None:
    refs = "origin/zeta\norigin/alpha\n"

    assert select_remote_branch(refs) == "alpha"


@pytest.mark.asyncio
async def test_resolve_default_branch_uses_local_metadata_first(
    tmp_path: Path,
) -> None:
    _write_remote_head(tmp_path, "develop")
    run_process = AsyncMock()

    branch = await resolve_remote_default_branch(
        run_process,
        tmp_path,
    )

    assert branch == "develop"
    run_process.assert_not_awaited()


@pytest.mark.asyncio
async def test_resolve_default_branch_falls_back_to_advertisement(
    tmp_path: Path,
) -> None:
    run_process = AsyncMock(
        side_effect=[
            RuntimeError("no local symbolic ref"),
            "ref: refs/heads/trunk\tHEAD\nabc123\tHEAD",
        ],
    )

    branch = await resolve_remote_default_branch(
        run_process,
        tmp_path,
    )

    assert branch == "trunk"
    assert run_process.await_args_list == [
        call(
            [
                "git",
                "symbolic-ref",
                "--quiet",
                "--short",
                "refs/remotes/origin/HEAD",
            ],
            cwd=tmp_path,
            timeout=30,
        ),
        call(
            ["git", "ls-remote", "--symref", "origin", "HEAD"],
            cwd=tmp_path,
            timeout=120,
        ),
    ]


@pytest.mark.asyncio
async def test_resolve_default_branch_falls_back_to_remote_refs(
    tmp_path: Path,
) -> None:
    run_process = AsyncMock(
        side_effect=[
            RuntimeError("no symbolic ref"),
            RuntimeError("remote HEAD unavailable"),
            "origin/release\norigin/master\n",
        ],
    )

    branch = await resolve_remote_default_branch(
        run_process,
        tmp_path,
    )

    assert branch == "master"


def test_build_research_branch_uses_issue_and_bounded_suffix() -> None:
    assert build_research_branch(_dialog()) == (
        "autoresearch/issue-42-planabc1"
    )


def test_build_research_branch_uses_task_without_issue() -> None:
    dialog = _dialog(
        goal="Develop a memory TTL feature",
        plan_markdown="# Plan",
    )

    assert build_research_branch(dialog) == (
        "autoresearch/issue-task-planabc1"
    )


def test_reusable_worktree_requires_resume_metadata(
    tmp_path: Path,
) -> None:
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    (worktree / ".git").write_text(
        "gitdir: somewhere",
        encoding="utf-8",
    )
    dialog = _dialog(
        worktree_path=str(worktree),
        branch="autoresearch/issue-42-planabc1",
        upstream_repository="agentscope-ai/QwenPaw",
        push_repository="kayky233/QwenPaw",
    )

    assert reusable_worktree(dialog) == worktree

    dialog.branch = ""
    with pytest.raises(
        RuntimeError,
        match="cannot be resumed safely",
    ):
        reusable_worktree(dialog)


@pytest.mark.asyncio
async def test_prepare_reuses_worktree_without_git_commands(
    tmp_path: Path,
) -> None:
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    (worktree / ".git").write_text(
        "gitdir: somewhere",
        encoding="utf-8",
    )
    dialog = _dialog(
        worktree_path=str(worktree),
        branch="autoresearch/issue-42-planabc1",
        upstream_repository="agentscope-ai/QwenPaw",
        push_repository="kayky233/QwenPaw",
    )
    run_process = AsyncMock()

    prepared = await prepare_research_worktree(
        dialog,
        package_root=tmp_path / "package",
        working_dir=tmp_path / "working",
        runtime_context={},
        run_process=run_process,
        github_repository=github_repository,
        github_remote_identity=github_remote_identity,
    )

    assert prepared == (
        worktree,
        dialog.branch,
        dialog.upstream_repository,
        dialog.push_repository,
    )
    run_process.assert_not_awaited()


@pytest.mark.asyncio
async def test_prepare_local_repository_uses_remote_default_branch(
    tmp_path: Path,
) -> None:
    package_root = tmp_path / "QwenPaw"
    _write_remote_head(package_root, "develop")
    runtime_context: dict = {}
    run_process = AsyncMock(
        side_effect=[
            "git@github.com:kayky233/QwenPaw.git",
            "",
            "",
        ],
    )

    prepared = await prepare_research_worktree(
        _dialog(),
        package_root=package_root,
        working_dir=tmp_path / "working",
        runtime_context=runtime_context,
        run_process=run_process,
        github_repository=github_repository,
        github_remote_identity=github_remote_identity,
    )

    worktree, branch, upstream, push = prepared
    assert branch == "autoresearch/issue-42-planabc1"
    assert upstream == "agentscope-ai/QwenPaw"
    assert push == "kayky233/QwenPaw"
    assert worktree == (
        package_root
        / ".qwenpaw"
        / "worktrees"
        / "research-planabc1"
    )
    assert runtime_context["base_branch"] == "develop"
    assert runtime_context["base_branch_source"] == (
        "local_remote_head"
    )
    assert runtime_context["source_root"] == str(package_root)
    assert call(
        ["git", "fetch", "origin", "develop"],
        cwd=package_root,
        timeout=600,
    ) in run_process.await_args_list
    assert call(
        [
            "git",
            "worktree",
            "add",
            str(worktree),
            "-b",
            branch,
            "origin/develop",
        ],
        cwd=package_root,
        timeout=120,
    ) in run_process.await_args_list


@pytest.mark.asyncio
async def test_prepare_local_repository_uses_legacy_main_fallback(
    tmp_path: Path,
) -> None:
    package_root = tmp_path / "QwenPaw"
    (package_root / ".git").mkdir(parents=True)
    runtime_context: dict = {}
    run_process = AsyncMock(
        side_effect=[
            "git@github.com:kayky233/QwenPaw.git",
            "",
            "",
        ],
    )

    await prepare_research_worktree(
        _dialog(),
        package_root=package_root,
        working_dir=tmp_path / "working",
        runtime_context=runtime_context,
        run_process=run_process,
        github_repository=github_repository,
        github_remote_identity=github_remote_identity,
    )

    assert runtime_context["base_branch"] == "main"
    assert runtime_context["base_branch_source"] == (
        "legacy_local_default"
    )
    assert run_process.await_args_list[1].args[0] == [
        "git",
        "fetch",
        "origin",
        "main",
    ]


@pytest.mark.asyncio
async def test_prepare_clones_repository_into_workspace_cache(
    tmp_path: Path,
) -> None:
    package_root = tmp_path / "different-package"
    package_root.mkdir()
    workspace_dir = tmp_path / "workspace"
    runtime_context = {
        "workspace": SimpleNamespace(
            workspace_dir=workspace_dir,
        ),
    }
    run_process = AsyncMock(
        side_effect=[
            "",
            "origin/master",
            "",
            "",
        ],
    )

    prepared = await prepare_research_worktree(
        _dialog(),
        package_root=package_root,
        working_dir=tmp_path / "working",
        runtime_context=runtime_context,
        run_process=run_process,
        github_repository=github_repository,
        github_remote_identity=github_remote_identity,
    )

    source_root = (
        workspace_dir
        / ".qwenpaw"
        / "research-repositories"
        / "agentscope-ai-QwenPaw"
    )
    assert prepared[0].is_relative_to(source_root)
    assert prepared[2:] == (
        "agentscope-ai/QwenPaw",
        "agentscope-ai/QwenPaw",
    )
    assert runtime_context["base_branch"] == "master"
    assert runtime_context["base_branch_source"] == (
        "remote_head"
    )
    assert run_process.await_args_list[0] == call(
        [
            "git",
            "clone",
            "https://github.com/agentscope-ai/QwenPaw.git",
            str(source_root),
        ],
        cwd=source_root.parent,
        timeout=600,
    )


def test_real_router_uses_extracted_worktree_service() -> None:
    from qwenpaw.app.routers import research as research_module

    assert research_module._build_research_branch is (
        build_research_branch
    )
    assert research_module._read_local_remote_head is (
        read_local_remote_head
    )
    assert research_module._reusable_worktree is reusable_worktree
    assert research_module._resolve_remote_default_branch is (
        resolve_remote_default_branch
    )
    assert research_module._prepare_research_worktree.__module__ == (
        "qwenpaw.app.routers.research_worktree_service"
    )


def test_installer_exposes_worktree_helpers() -> None:
    module = SimpleNamespace(
        DialogRunState=SimpleNamespace,
        _dialog_runtime_context={},
        _run_process=AsyncMock(),
        _github_repository=github_repository,
        _github_remote_identity=github_remote_identity,
        WORKING_DIR=Path("/tmp/qwenpaw"),
        __file__=__file__,
    )

    install_research_worktree_service(module)

    assert module._validate_git_branch_name is (
        validate_git_branch_name
    )
    assert module._read_local_remote_head is read_local_remote_head
    assert module._build_research_branch is build_research_branch
