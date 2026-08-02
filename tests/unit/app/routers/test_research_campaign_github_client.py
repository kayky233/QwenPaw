from __future__ import annotations

from types import SimpleNamespace

import pytest

from qwenpaw.app.routers import research_campaign_github_client as client_module


def _arguments():
    return {
        "repository": "owner/repository",
        "base": "main",
        "head": "owner:autoresearch/issue-1-run",
        "title": "fix: issue",
        "body": "verified",
        "draft": True,
    }


def test_unauthenticated_gh_falls_back_to_token_api(monkeypatch) -> None:
    monkeypatch.setattr(client_module.shutil, "which", lambda _: "/usr/bin/gh")
    monkeypatch.setattr(
        client_module,
        "_github_cli_authentication",
        lambda: (False, "not_authenticated"),
    )
    calls = []

    def request(url, **kwargs):
        calls.append((url, kwargs))
        return {"html_url": "https://github.com/owner/repository/pull/2", "number": 2}

    monkeypatch.setattr(client_module.campaign_runtime, "_github_json_request", request)
    monkeypatch.setattr(
        client_module.subprocess,
        "run",
        lambda *args, **kwargs: pytest.fail("unauthenticated gh must not run"),
    )
    client = client_module.AuthenticatedGitHubChangeRequestClient(
        environment={"GITHUB_TOKEN": "token-value"}
    )

    result = client.create_pull_request(**_arguments())

    assert result["number"] == 2
    assert calls[0][1]["token"] == "token-value"
    assert calls[0][1]["payload"]["draft"] is True


def test_authenticated_gh_is_used_without_exposing_token(monkeypatch) -> None:
    monkeypatch.setattr(client_module.shutil, "which", lambda _: "/usr/bin/gh")
    monkeypatch.setattr(
        client_module,
        "_github_cli_authentication",
        lambda: (True, "authenticated"),
    )
    observed = []

    def run(argv, **kwargs):
        observed.append((argv, kwargs))
        return SimpleNamespace(
            returncode=0,
            stdout="https://github.com/owner/repository/pull/3\n",
            stderr="",
        )

    monkeypatch.setattr(client_module.subprocess, "run", run)
    client = client_module.AuthenticatedGitHubChangeRequestClient(
        environment={
            "PATH": "/usr/bin",
            "HOME": "/tmp/home",
            "GITHUB_TOKEN": "must-not-enter-gh-environment",
        }
    )

    result = client.create_pull_request(**_arguments())

    assert result["number"] == 3
    argv, kwargs = observed[0]
    assert argv[:3] == ["/usr/bin/gh", "pr", "create"]
    assert "--draft" in argv
    assert "GITHUB_TOKEN" not in kwargs["env"]


def test_client_requires_authenticated_cli_or_token(monkeypatch) -> None:
    monkeypatch.setattr(client_module.shutil, "which", lambda _: None)
    monkeypatch.setattr(
        client_module,
        "_github_cli_authentication",
        lambda: (False, "not_installed"),
    )
    client = client_module.AuthenticatedGitHubChangeRequestClient(environment={})

    with pytest.raises(RuntimeError, match="authenticated gh CLI"):
        client.create_pull_request(**_arguments())
