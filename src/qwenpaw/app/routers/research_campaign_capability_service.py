"""Harden direct Campaign runtime capability discovery."""

from __future__ import annotations

import os
import shutil
import subprocess
from types import ModuleType
from typing import Any

from . import research_campaign_direct_service as direct_service

_GH_STATUS_ENVIRONMENT = frozenset(
    {
        "PATH",
        "HOME",
        "USERPROFILE",
        "GH_CONFIG_DIR",
        "SYSTEMROOT",
        "WINDIR",
        "COMSPEC",
        "PATHEXT",
        "SSL_CERT_FILE",
        "REQUESTS_CA_BUNDLE",
        "CURL_CA_BUNDLE",
    }
)


def _github_cli_authentication() -> tuple[bool, str]:
    executable = shutil.which("gh")
    if executable is None:
        return False, "not_installed"
    try:
        completed = subprocess.run(
            [executable, "auth", "status", "--hostname", "github.com", "--active"],
            capture_output=True,
            text=True,
            check=False,
            timeout=15,
            env={
                key: value
                for key, value in os.environ.items()
                if key in _GH_STATUS_ENVIRONMENT
            },
        )
    except (OSError, subprocess.SubprocessError):
        return False, "status_check_failed"
    return (
        completed.returncode == 0,
        "authenticated" if completed.returncode == 0 else "not_authenticated",
    )


def hardened_runtime_info(
    research_module: ModuleType,
    data: Any,
) -> dict[str, Any]:
    raw_agents = data.get("agents", []) if isinstance(data, dict) else []
    agents = [
        direct_service._agent_payload(item)
        for item in raw_agents
        if isinstance(item, dict) and str(item.get("id", "")).strip()
    ]
    ready_agents = [
        item
        for item in agents
        if item["enabled"]
        and item["startup_status"].casefold() == "running"
        and item["workspace_dir"]
    ]
    unavailable_agents = [item for item in agents if item not in ready_agents]
    git_available = shutil.which("git") is not None
    github_cli_available = shutil.which("gh") is not None
    github_cli_authenticated, github_cli_status = _github_cli_authentication()
    github_token_available = bool(
        os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    )
    remote_delivery_available = git_available and (
        github_cli_authenticated or github_token_available
    )
    return {
        "available": git_available,
        "unsafe_execution_enabled": research_module.unsafe_research_enabled(),
        "delivery_modes": ["local", "draft_pr"],
        "default_delivery_mode": "local",
        "automatic_merge": False,
        "git_available": git_available,
        "github_cli_available": github_cli_available,
        "github_cli_authenticated": github_cli_authenticated,
        "github_cli_status": github_cli_status,
        "github_token_available": github_token_available,
        "remote_delivery_available": remote_delivery_available,
        "agents": ready_agents,
        "unavailable_agents": unavailable_agents,
    }


def install_research_campaign_capability_service(
    research_module: ModuleType,
) -> None:
    """Replace permissive capability and PR execution with authenticated checks."""

    if getattr(research_module, "_campaign_capability_service_installed", False):
        return
    from . import research_campaign_runtime as campaign_runtime
    from .research_campaign_github_client import (
        AuthenticatedGitHubChangeRequestClient,
    )

    direct_service._runtime_info = hardened_runtime_info
    campaign_runtime._GitHubChangeRequestClient = (
        AuthenticatedGitHubChangeRequestClient
    )
    research_module._campaign_capability_service_installed = True
