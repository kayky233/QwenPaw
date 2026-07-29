"""Planning policies extracted from the monolithic AutoResearch router."""

from __future__ import annotations

import platform
import re
import sys
from pathlib import Path
from types import ModuleType
from typing import Any, Callable

EnvironmentResolver = Callable[
    [str | None, str | None],
    tuple[str, str],
]


def current_research_environment(
    host_platform: str | None = None,
    host_machine: str | None = None,
) -> tuple[str, str]:
    """Return a canonical platform key and a human-readable environment."""

    raw_platform = (host_platform or sys.platform).casefold()
    machine = (
        host_machine
        or platform.machine()
        or "unknown"
    ).strip()
    if raw_platform.startswith("win"):
        canonical = "windows"
        label = "Windows"
    elif raw_platform == "darwin":
        canonical = "macos"
        label = "macOS"
    elif raw_platform.startswith("linux"):
        canonical = "linux"
        label = "Linux"
    else:
        canonical = raw_platform or "unknown"
        label = host_platform or sys.platform or "unknown"
    return canonical, f"{label} ({machine})"


def plan_environment_compatibility(
    plan_markdown: str,
    *,
    host_platform: str | None = None,
    host_machine: str | None = None,
    environment_resolver: EnvironmentResolver = (
        current_research_environment
    ),
) -> tuple[str, str, str]:
    """Classify whether the approved plan can run on the current host."""

    current_platform, current_environment = environment_resolver(
        host_platform,
        host_machine,
    )
    match = re.search(
        r"(?ims)^##\s+Reproduction Environment\s*$\s*(.*?)"
        r"(?=^##\s+|\Z)",
        plan_markdown,
    )
    if match is None:
        return (
            "unknown",
            current_environment,
            "计划未声明复现环境，请在批准前补充 "
            "Reproduction Environment。",
        )

    environment = match.group(1).casefold()
    if re.search(
        r"\b(?:cross[- ]platform|platform[- ]independent|"
        r"any\s+os|all\s+platforms)\b",
        environment,
    ):
        return (
            "compatible",
            current_environment,
            f"计划声明为跨平台，可在当前 {current_environment} 环境验证。",
        )

    required: set[str] = set()
    if re.search(
        r"\bwindows\b|\bwin(?:dows)?\s*1[01]\b|\.exe\b",
        environment,
    ):
        required.add("windows")
    if re.search(
        r"\bmacos\b|\bmac\s*os\b|\bdarwin\b|\bos\s*x\b",
        environment,
    ):
        required.add("macos")
    if re.search(
        r"\blinux\b|\bubuntu\b|\bdebian\b|\bfedora\b|"
        r"\bcentos\b",
        environment,
    ):
        required.add("linux")

    if not required:
        return (
            "unknown",
            current_environment,
            "计划中的复现环境无法自动判定，请人工核对后再批准。",
        )
    if current_platform in required:
        return (
            "compatible",
            current_environment,
            f"计划要求的复现平台包含当前 {current_environment}。",
        )

    labels = {
        "windows": "Windows",
        "macos": "macOS",
        "linux": "Linux",
    }
    required_label = "/".join(
        labels[item]
        for item in sorted(required)
    )
    return (
        "incompatible",
        current_environment,
        f"计划要求 {required_label}，但当前执行环境是 "
        f"{current_environment}；请选择能在当前环境复现和验证的问题。",
    )


def task_title(program: str, fallback: str) -> str:
    """Use the first Markdown heading as the display title."""

    for line in program.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            return stripped.lstrip("#").strip() or fallback
    return fallback


def derive_task_id(goal: str) -> str:
    """Derive the current bounded kebab-case task ID from a goal."""

    words = goal.strip().lower().split()[:6]
    slug = "-".join(
        normalized
        for word in words
        if (normalized := re.sub(r"[^a-z0-9-]", "", word))
    )
    return slug[:60] or "auto-task"


def extract_phase_content(
    raw_response: str,
    filename: str,
) -> str:
    """Extract one phase artifact from markers or a fenced block."""

    if not raw_response or not raw_response.strip():
        return raw_response

    escaped = re.escape(filename)
    tag_match = re.search(
        rf"<<<FILE:\s*{escaped}\s*>>>\s*\n(.*?)<<<END>>>",
        raw_response,
        re.DOTALL | re.IGNORECASE,
    )
    if tag_match:
        content = tag_match.group(1).strip()
        if content:
            return content

    fence_match = re.search(
        rf"```(?:python|py|{escaped})\s*\n(.*?)```",
        raw_response,
        re.DOTALL | re.IGNORECASE,
    )
    if fence_match:
        content = fence_match.group(1).strip()
        if content:
            return content
    return raw_response


def execution_prompt(dialog: Any, worktree: Path) -> str:
    """Build the constrained implementation prompt for an approved plan."""

    previous_feedback = ""
    if dialog.validation_attempts or dialog.validation_report:
        unapproved = "\n".join(
            f"- {path}"
            for path in dialog.unapproved_paths
        ) or "- none"
        previous_feedback = f"""

Previous validation feedback
----------------------------
This is a resumed attempt. Preserve valid changes already present in the
worktree and address the validation feedback instead of starting over.

Previously unapproved paths:
{unapproved}

Latest validation report:
{dialog.validation_report[-12_000:]}
"""
    return f"""You are executing an already approved repository plan.

Approved revision: {dialog.approved_revision}
Approved SHA-256: {dialog.approved_content_hash}
Repository worktree: {worktree}

APPROVED PLAN
{dialog.plan_markdown}
{previous_feedback}

Requirements:
1. Inspect the issue and current code before editing.
2. Make the smallest correct change entirely inside the worktree.
3. Add or update focused regression tests that reproduce the issue's public,
   observable behavior using the approved environment and configuration.
   Prefer local fakes or mocks for credentials, processes, and network calls.
   Do not merely assert private implementation details when a behavior-level
   reproduction is feasible.
4. Do not run tests yourself; the host runs approved tests in a deny-default
   sandbox.
5. Do not commit, push, create a pull request, or edit files outside the
   worktree.
6. Only create or edit files listed under ``Modifiable Files``. Keep every
   frozen file byte-for-byte unchanged.
7. Do not create documentation, TODO files, ``.gitignore`` changes, lockfiles,
   skill metadata, generated files, or any other file outside the approved
   scope. If the issue is already implemented, add only the focused regression
   tests listed in the plan.
8. Finish only after the approved changes and tests are present on disk.
"""


def install_research_planning_service(
    research_module: ModuleType,
) -> None:
    """Install planning policies while retaining Router test seams."""

    def compatibility(
        plan_markdown: str,
        *,
        host_platform: str | None = None,
        host_machine: str | None = None,
    ) -> tuple[str, str, str]:
        return plan_environment_compatibility(
            plan_markdown,
            host_platform=host_platform,
            host_machine=host_machine,
            environment_resolver=(
                research_module._current_research_environment
            ),
        )

    research_module._current_research_environment = (
        current_research_environment
    )
    research_module._plan_environment_compatibility = compatibility
    research_module._task_title = task_title
    research_module._derive_task_id = derive_task_id
    research_module._extract_phase_content = extract_phase_content
    research_module._execution_prompt = execution_prompt
