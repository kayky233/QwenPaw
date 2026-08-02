from __future__ import annotations

from types import SimpleNamespace

from qwenpaw.app.routers import research_campaign_capability_service as service


def _module(enabled: bool = True):
    return SimpleNamespace(unsafe_research_enabled=lambda: enabled)


def _agents():
    return {
        "agents": [
            {
                "id": "implementer",
                "enabled": True,
                "startup_status": "running",
                "workspace_dir": "/tmp/implementer",
            },
            {
                "id": "reviewer",
                "enabled": True,
                "startup_status": "failed",
                "workspace_dir": "/tmp/reviewer",
            },
        ]
    }


def test_runtime_info_requires_authenticated_cli_or_token(monkeypatch) -> None:
    monkeypatch.setattr(service.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(
        service,
        "_github_cli_authentication",
        lambda: (False, "not_authenticated"),
    )
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)

    info = service.hardened_runtime_info(_module(), _agents())

    assert info["git_available"] is True
    assert info["github_cli_available"] is True
    assert info["github_cli_authenticated"] is False
    assert info["remote_delivery_available"] is False
    assert [item["id"] for item in info["agents"]] == ["implementer"]
    assert [item["id"] for item in info["unavailable_agents"]] == ["reviewer"]


def test_runtime_info_accepts_authenticated_cli(monkeypatch) -> None:
    monkeypatch.setattr(service.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(
        service,
        "_github_cli_authentication",
        lambda: (True, "authenticated"),
    )
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)

    info = service.hardened_runtime_info(_module(), _agents())

    assert info["github_cli_authenticated"] is True
    assert info["remote_delivery_available"] is True
    assert info["automatic_merge"] is False


def test_runtime_info_accepts_token_without_gh(monkeypatch) -> None:
    monkeypatch.setattr(
        service.shutil,
        "which",
        lambda name: "/usr/bin/git" if name == "git" else None,
    )
    monkeypatch.setattr(
        service,
        "_github_cli_authentication",
        lambda: (False, "not_installed"),
    )
    monkeypatch.setenv("GITHUB_TOKEN", "not-exposed")

    info = service.hardened_runtime_info(_module(), _agents())

    assert info["github_cli_available"] is False
    assert info["github_token_available"] is True
    assert info["remote_delivery_available"] is True
    assert "not-exposed" not in repr(info)
