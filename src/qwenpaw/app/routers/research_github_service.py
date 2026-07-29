"""Pure GitHub helpers extracted from the AutoResearch router."""

from __future__ import annotations

import re
from types import ModuleType
from typing import Any

_GITHUB_REPOSITORY_RE = re.compile(
    r"https://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?"
    r"(?:[/\s?#]|$)",
)
_GITHUB_REMOTE_RE = re.compile(
    r"(?:https?://github\.com/|ssh://git@github\.com/|git@github\.com:)"
    r"([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?/?",
    re.IGNORECASE,
)


def github_repository(dialog: Any) -> tuple[str, str, str]:
    """Resolve the approved GitHub repository and canonical clone URL."""

    text = f"{dialog.goal}\n{dialog.plan_markdown or ''}"
    match = _GITHUB_REPOSITORY_RE.search(text)
    if match is None:
        raise RuntimeError(
            "Approved plan does not contain a GitHub repository URL",
        )
    owner, repository = match.groups()
    return (
        owner,
        repository,
        f"https://github.com/{owner}/{repository}.git",
    )


def github_remote_identity(remote_url: str) -> tuple[str, str] | None:
    """Parse supported GitHub HTTPS and SSH remote URL forms."""

    match = _GITHUB_REMOTE_RE.fullmatch(remote_url.strip())
    if match is None:
        return None
    return match.group(1), match.group(2)


def build_dialog_pr_body(dialog: Any) -> str:
    """Build a PR body that always carries the validation evidence."""

    if not dialog.validation_report.strip():
        raise RuntimeError("Cannot create a PR without a validation report")
    return f"""## AutoResearch Result

- Goal: {dialog.goal}
- Branch: `{dialog.branch}`
- Commit: `{dialog.commit_sha}`

---

{dialog.validation_report.strip()}
"""


def install_research_github_service(research_module: ModuleType) -> None:
    """Install pure GitHub helpers without changing router endpoints."""

    research_module._github_repository = github_repository
    research_module._github_remote_identity = github_remote_identity
    research_module._build_dialog_pr_body = build_dialog_pr_body
