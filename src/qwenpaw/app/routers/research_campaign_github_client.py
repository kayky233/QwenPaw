"""Authentication-aware GitHub Draft PR client for Issue Campaigns."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any
from urllib.request import urlopen

from . import research_campaign_runtime as campaign_runtime
from .research_campaign_capability_service import (
    _GH_STATUS_ENVIRONMENT,
    _github_cli_authentication,
)


class AuthenticatedGitHubChangeRequestClient:
    """Use authenticated gh when available, otherwise the explicit token API."""

    def __init__(
        self,
        *,
        environment: dict[str, str] | None = None,
        urlopen_func: Any = urlopen,
    ) -> None:
        self.environment = environment if environment is not None else os.environ
        self.urlopen_func = urlopen_func

    def _gh_environment(self) -> dict[str, str]:
        return {
            key: value
            for key, value in self.environment.items()
            if key in _GH_STATUS_ENVIRONMENT
        }

    def _create_with_gh(
        self,
        *,
        repository: str,
        base: str,
        head: str,
        title: str,
        body: str,
        draft: bool,
    ) -> dict[str, Any]:
        executable = shutil.which("gh")
        authenticated, _ = _github_cli_authentication()
        if executable is None or not authenticated:
            raise RuntimeError("authenticated GitHub CLI is unavailable")
        with tempfile.TemporaryDirectory(prefix="qwenpaw-campaign-pr-") as temp:
            body_file = Path(temp) / "body.md"
            body_file.write_text(body, encoding="utf-8")
            argv = [
                executable,
                "pr",
                "create",
                "--repo",
                repository,
                "--base",
                base,
                "--head",
                head,
                "--title",
                title,
                "--body-file",
                str(body_file),
            ]
            if draft:
                argv.append("--draft")
            completed = subprocess.run(
                argv,
                capture_output=True,
                text=True,
                check=False,
                timeout=120,
                env=self._gh_environment(),
            )
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout).strip()
            raise RuntimeError(f"GitHub CLI PR creation failed: {detail}")
        url = completed.stdout.strip().splitlines()[-1].strip()
        if not url:
            raise RuntimeError("GitHub CLI did not return a PR URL")
        number_match = re.search(r"/pull/(\d+)(?:$|[/?#])", url)
        return {
            "html_url": url,
            "number": int(number_match.group(1)) if number_match else None,
        }

    def _create_with_token(
        self,
        *,
        repository: str,
        base: str,
        head: str,
        title: str,
        body: str,
        draft: bool,
    ) -> dict[str, Any]:
        token = self.environment.get("GITHUB_TOKEN") or self.environment.get(
            "GH_TOKEN"
        )
        if not token:
            raise RuntimeError(
                "campaign delivery requires authenticated gh CLI or "
                "GITHUB_TOKEN/GH_TOKEN"
            )
        return campaign_runtime._github_json_request(
            f"https://api.github.com/repos/{repository}/pulls",
            token=token,
            method="POST",
            payload={
                "title": title,
                "head": head,
                "base": base,
                "body": body,
                "draft": draft,
            },
            urlopen_func=self.urlopen_func,
        )

    def create_pull_request(
        self,
        *,
        repository: str,
        base: str,
        head: str,
        title: str,
        body: str,
        draft: bool,
    ) -> dict[str, Any]:
        authenticated, _ = _github_cli_authentication()
        if shutil.which("gh") is not None and authenticated:
            return self._create_with_gh(
                repository=repository,
                base=base,
                head=head,
                title=title,
                body=body,
                draft=draft,
            )
        return self._create_with_token(
            repository=repository,
            base=base,
            head=head,
            title=title,
            body=body,
            draft=draft,
        )
