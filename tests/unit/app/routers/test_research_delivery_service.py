from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from qwenpaw.app.routers.research_delivery_service import (
    build_pull_request_payload,
    create_dialog_pr,
    dialog_pr_head,
    dialog_pr_title,
    install_research_delivery_service,
    push_research_branch,
    resolve_delivery_base_branch,
    validate_github_repository_name,
)


def _dialog(**overrides):
    values = {
        "plan_id": "plan-delivery",
        "goal": (
            "Fix "
            "https://github.com/agentscope-ai/QwenPaw/issues/42"
        ),
        "plan_markdown": "# Plan\n",
        "upstream_repository": "agentscope-ai/QwenPaw",
        "push_repository": "kayky233/QwenPaw",
        "branch": "autoresearch/issue-42-plandel1",
        "commit_sha": "a" * 40,
        "validation_report": "# Validation\n\nPassed.",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


@pytest.mark.parametrize(
    "repository",
    [
        "missing-slash",
        "/repo",
        "owner/",
        "owner/repo/extra",
        "owner/repo with space",
    ],
)
def test_validate_github_repository_name_rejects_unsafe_values(
    repository: str,
) -> None:
    with pytest.raises(
        RuntimeError,
        match="Invalid GitHub repository",
    ):
        validate_github_repository_name(repository)


def test_dialog_pr_head_uses_fork_owner_when_needed() -> None:
    assert dialog_pr_head(_dialog()) == (
        "kayky233:autoresearch/issue-42-plandel1"
    )


def test_dialog_pr_head_uses_plain_branch_for_same_repository() -> None:
    dialog = _dialog(
        push_repository="agentscope-ai/QwenPaw",
    )

    assert dialog_pr_head(dialog) == (
        "autoresearch/issue-42-plandel1"
    )


def test_dialog_pr_head_qualifies_same_owner_different_repository() -> None:
    dialog = _dialog(
        upstream_repository="agentscope-ai/QwenPaw",
        push_repository="agentscope-ai/QwenPaw-fork",
    )

    assert dialog_pr_head(dialog) == (
        "agentscope-ai:autoresearch/issue-42-plandel1"
    )


def test_dialog_pr_title_prefers_explicit_issue_number() -> None:
    assert dialog_pr_title(_dialog()) == (
        "fix: resolve issue #42"
    )


def test_dialog_pr_title_normalizes_non_issue_goal() -> None:
    dialog = _dialog(
        goal="  Add   memory TTL support  ",
        plan_markdown="# Feature plan",
    )

    assert dialog_pr_title(dialog) == (
        "fix: Add memory TTL support"
    )


def test_build_pull_request_payload_binds_dynamic_base_branch() -> None:
    payload = build_pull_request_payload(
        _dialog(),
        base_branch="develop",
        body="# Evidence",
    )

    assert payload == {
        "title": "fix: resolve issue #42",
        "head": (
            "kayky233:autoresearch/issue-42-plandel1"
        ),
        "base": "develop",
        "body": "# Evidence",
    }


@pytest.mark.asyncio
async def test_resolve_delivery_base_branch_uses_cached_branch(
    tmp_path: Path,
) -> None:
    runtime_context = {"base_branch": "release/v2"}
    run_process = AsyncMock()

    branch = await resolve_delivery_base_branch(
        runtime_context,
        worktree=tmp_path,
        run_process=run_process,
    )

    assert branch == "release/v2"
    run_process.assert_not_awaited()


@pytest.mark.asyncio
async def test_resolve_delivery_base_branch_recovers_from_remote_head(
    tmp_path: Path,
) -> None:
    runtime_context = {"source_root": str(tmp_path)}
    run_process = AsyncMock(return_value="origin/master")

    branch = await resolve_delivery_base_branch(
        runtime_context,
        worktree=tmp_path,
        run_process=run_process,
    )

    assert branch == "master"
    assert runtime_context["base_branch"] == "master"
    assert runtime_context["base_branch_source"] == "remote_head"


@pytest.mark.asyncio
async def test_resolve_delivery_base_branch_preserves_legacy_main(
    tmp_path: Path,
) -> None:
    runtime_context: dict = {}
    run_process = AsyncMock()

    branch = await resolve_delivery_base_branch(
        runtime_context,
        worktree=tmp_path,
        run_process=run_process,
    )

    assert branch == "main"
    assert runtime_context["base_branch_source"] == (
        "legacy_default"
    )
    run_process.assert_not_awaited()


@pytest.mark.asyncio
async def test_push_research_branch_validates_and_pushes(
    tmp_path: Path,
) -> None:
    run_process = AsyncMock(return_value="")

    await push_research_branch(
        tmp_path,
        "autoresearch/issue-42-plandel1",
        run_process=run_process,
    )

    run_process.assert_awaited_once_with(
        [
            "git",
            "push",
            "-u",
            "origin",
            "autoresearch/issue-42-plandel1",
        ],
        cwd=tmp_path,
        timeout=600,
    )


@pytest.mark.asyncio
async def test_create_dialog_pr_cli_uses_dynamic_base_branch(
    tmp_path: Path,
) -> None:
    dialog = _dialog()
    run_process = AsyncMock(
        return_value=(
            "https://github.com/agentscope-ai/"
            "QwenPaw/pull/9\n"
        ),
    )

    pr_url = await create_dialog_pr(
        tmp_path,
        dialog,
        runtime_context={"base_branch": "develop"},
        run_process=run_process,
        build_body=lambda _: "# Validation evidence",
        executable_lookup=(
            lambda name: "/usr/bin/gh"
            if name == "gh"
            else None
        ),
        environment={},
    )

    assert pr_url == (
        "https://github.com/agentscope-ai/QwenPaw/pull/9"
    )
    args = run_process.await_args.args[0]
    assert args[:4] == ["gh", "pr", "create", "--repo"]
    assert args[args.index("--base") + 1] == "develop"
    assert args[args.index("--head") + 1] == (
        "kayky233:autoresearch/issue-42-plandel1"
    )
    assert args[args.index("--title") + 1] == (
        "fix: resolve issue #42"
    )
    run_process.assert_awaited_once()


@pytest.mark.asyncio
async def test_create_dialog_pr_rest_uses_payload_and_token(
    tmp_path: Path,
) -> None:
    captured: dict = {}

    def rest_creator(
        repository: str,
        payload: dict[str, str],
        token: str,
    ) -> str:
        captured.update(
            repository=repository,
            payload=payload,
            token=token,
        )
        return (
            "https://github.com/agentscope-ai/"
            "QwenPaw/pull/10"
        )

    pr_url = await create_dialog_pr(
        tmp_path,
        _dialog(),
        runtime_context={"base_branch": "master"},
        run_process=AsyncMock(),
        build_body=lambda _: "# Validation evidence",
        executable_lookup=lambda _: None,
        environment={"GITHUB_TOKEN": "secret-token"},
        rest_creator=rest_creator,
    )

    assert pr_url.endswith("/pull/10")
    assert captured["repository"] == (
        "agentscope-ai/QwenPaw"
    )
    assert captured["token"] == "secret-token"
    assert captured["payload"]["base"] == "master"
    assert captured["payload"]["head"] == (
        "kayky233:autoresearch/issue-42-plandel1"
    )


@pytest.mark.asyncio
async def test_create_dialog_pr_requires_delivery_credentials(
    tmp_path: Path,
) -> None:
    with pytest.raises(
        RuntimeError,
        match="install and authenticate GitHub CLI",
    ):
        await create_dialog_pr(
            tmp_path,
            _dialog(),
            runtime_context={"base_branch": "main"},
            run_process=AsyncMock(),
            build_body=lambda _: "# Validation evidence",
            executable_lookup=lambda _: None,
            environment={},
        )


def test_installer_exposes_delivery_helpers() -> None:
    module = SimpleNamespace(
        _run_process=AsyncMock(),
        _dialog_runtime_context={},
        _build_dialog_pr_body=lambda _: "# Evidence",
    )

    install_research_delivery_service(module)

    assert module._dialog_pr_head is dialog_pr_head
    assert module._dialog_pr_title is dialog_pr_title
    assert module._build_pull_request_payload is (
        build_pull_request_payload
    )


def test_real_router_uses_extracted_delivery_service() -> None:
    from qwenpaw.app.routers import research as research_module

    assert research_module._dialog_pr_head is dialog_pr_head
    assert research_module._dialog_pr_title is dialog_pr_title
    assert research_module._build_pull_request_payload is (
        build_pull_request_payload
    )
    assert research_module._push_research_branch.__module__ == (
        "qwenpaw.app.routers.research_delivery_service"
    )
    assert research_module._create_dialog_pr.__module__ == (
        "qwenpaw.app.routers.research_delivery_service"
    )
