from types import SimpleNamespace

import pytest

from qwenpaw.app.routers.research_github_service import (
    build_dialog_pr_body,
    github_remote_identity,
    github_repository,
    install_research_github_service,
)


def test_github_repository_is_resolved_from_goal() -> None:
    dialog = SimpleNamespace(
        goal="Fix https://github.com/kayky233/QwenPaw/issues/6470",
        plan_markdown="",
    )

    assert github_repository(dialog) == (
        "kayky233",
        "QwenPaw",
        "https://github.com/kayky233/QwenPaw.git",
    )


def test_github_repository_is_resolved_from_approved_plan() -> None:
    dialog = SimpleNamespace(
        goal="Fix the approved issue",
        plan_markdown=(
            "Repository: https://github.com/agentscope-ai/QwenPaw.git\n"
            "Issue: #6470"
        ),
    )

    assert github_repository(dialog) == (
        "agentscope-ai",
        "QwenPaw",
        "https://github.com/agentscope-ai/QwenPaw.git",
    )


def test_github_repository_requires_an_explicit_url() -> None:
    dialog = SimpleNamespace(
        goal="Fix issue 6470",
        plan_markdown="Repository: QwenPaw",
    )

    with pytest.raises(RuntimeError, match="does not contain"):
        github_repository(dialog)


@pytest.mark.parametrize(
    ("remote", "expected"),
    [
        (
            "https://github.com/kayky233/QwenPaw.git",
            ("kayky233", "QwenPaw"),
        ),
        (
            "git@github.com:kayky233/QwenPaw.git",
            ("kayky233", "QwenPaw"),
        ),
        (
            "ssh://git@github.com/kayky233/QwenPaw",
            ("kayky233", "QwenPaw"),
        ),
    ],
)
def test_github_remote_identity_supports_https_and_ssh(
    remote: str,
    expected: tuple[str, str],
) -> None:
    assert github_remote_identity(remote) == expected


def test_github_remote_identity_rejects_non_github_remote() -> None:
    assert github_remote_identity("https://gitlab.com/kayky233/QwenPaw.git") is None
    assert github_remote_identity("file:///tmp/QwenPaw") is None


def test_pr_body_requires_validation_evidence() -> None:
    dialog = SimpleNamespace(
        goal="Fix issue",
        branch="autoresearch/issue-1-test",
        commit_sha="abc123",
        validation_report="",
    )

    with pytest.raises(RuntimeError, match="validation report"):
        build_dialog_pr_body(dialog)


def test_pr_body_contains_delivery_identity_and_report() -> None:
    dialog = SimpleNamespace(
        goal="Fix issue 6470",
        branch="autoresearch/issue-6470-test",
        commit_sha="abc123def456",
        validation_report="\n# Validation\n\nFocused tests passed.\n",
    )

    body = build_dialog_pr_body(dialog)

    assert "Fix issue 6470" in body
    assert "`autoresearch/issue-6470-test`" in body
    assert "`abc123def456`" in body
    assert "# Validation" in body
    assert "Focused tests passed." in body


def test_installer_exposes_github_helpers_on_legacy_router() -> None:
    module = SimpleNamespace()

    install_research_github_service(module)

    assert module._github_repository is github_repository
    assert module._github_remote_identity is github_remote_identity
    assert module._build_dialog_pr_body is build_dialog_pr_body


def test_real_router_preserves_github_helper_behavior() -> None:
    from qwenpaw.app.routers import research as research_module

    dialog = SimpleNamespace(
        goal="Fix https://github.com/kayky233/QwenPaw/issues/6470",
        plan_markdown="",
    )
    assert research_module._github_repository(dialog) == github_repository(dialog)
    assert research_module._github_remote_identity(
        "git@github.com:kayky233/QwenPaw.git"
    ) == ("kayky233", "QwenPaw")

    delivery = SimpleNamespace(
        goal="Fix issue 6470",
        branch="autoresearch/issue-6470-test",
        commit_sha="abc123def456",
        validation_report="# Validation\n\nFocused tests passed.",
        plan_markdown="Task Type: bug_fix",
    )
    body = research_module._build_dialog_pr_body(delivery)
    assert "Focused tests passed." in body
    assert "abc123def456" in body
