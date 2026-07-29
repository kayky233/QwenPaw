"""Strict parsing and installation of AutoResearch plan file scopes."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from types import ModuleType
from typing import Any

_SECTION_RE_TEMPLATE = r"(?ims)^##\s+{heading}\s*$\n(.*?)(?=^##\s+|\Z)"
_LIST_ITEM_RE = re.compile(r"^\s*[-*+]\s+(.+?)\s*$")
_DRIVE_PATH_RE = re.compile(r"^[A-Za-z]:/")
_WILDCARD_CHARS = frozenset("*?[]{}")


class ResearchPlanScopeError(ValueError):
    """Raised when an approved plan contains an unsafe or ambiguous scope."""


@dataclass(frozen=True)
class ResearchPlanScope:
    """Normalized file scope extracted from a research plan."""

    modifiable_paths: frozenset[str]
    frozen_paths: frozenset[str]
    legacy_syntax: bool = False


def _section_body(plan_markdown: str, heading: str) -> str | None:
    pattern = re.compile(
        _SECTION_RE_TEMPLATE.format(heading=re.escape(heading)),
    )
    matches = pattern.findall(plan_markdown)
    if len(matches) > 1:
        raise ResearchPlanScopeError(
            f"Research plan contains duplicate '## {heading}' sections",
        )
    return matches[0].strip() if matches else None


def _normalize_scope_path(raw_path: str, *, heading: str) -> str:
    value = raw_path.strip()
    if value.startswith("`") or value.endswith("`"):
        if len(value) < 2 or not (value.startswith("`") and value.endswith("`")):
            raise ResearchPlanScopeError(
                f"Malformed backtick path in '## {heading}': {raw_path!r}",
            )
        value = value[1:-1].strip()

    value = value.replace("\\", "/")
    if not value or "\x00" in value:
        raise ResearchPlanScopeError(
            f"Empty or invalid path in '## {heading}'",
        )
    if value.startswith("/") or _DRIVE_PATH_RE.match(value):
        raise ResearchPlanScopeError(
            f"Absolute path is not allowed in '## {heading}': {raw_path!r}",
        )
    if any(char in value for char in _WILDCARD_CHARS):
        raise ResearchPlanScopeError(
            f"Wildcard path is not allowed in '## {heading}': {raw_path!r}",
        )
    if value.endswith("/"):
        raise ResearchPlanScopeError(
            f"Directory path is not allowed in '## {heading}': {raw_path!r}",
        )

    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ResearchPlanScopeError(
            f"Unsafe repository path in '## {heading}': {raw_path!r}",
        )
    if ":" in path.as_posix():
        raise ResearchPlanScopeError(
            f"Repository path cannot contain ':' in '## {heading}': {raw_path!r}",
        )
    return path.as_posix()


def _parse_section_paths(body: str | None, *, heading: str) -> frozenset[str]:
    if body is None:
        return frozenset()

    parsed: list[str] = []
    seen: set[str] = set()
    for line in body.splitlines():
        if not line.strip():
            continue
        match = _LIST_ITEM_RE.match(line)
        if match is None:
            continue
        path = _normalize_scope_path(match.group(1), heading=heading)
        if path in seen:
            raise ResearchPlanScopeError(
                f"Duplicate path in '## {heading}': {path}",
            )
        seen.add(path)
        parsed.append(path)
    return frozenset(parsed)


def _legacy_modifiable_paths(plan_markdown: str) -> frozenset[str]:
    """Read the narrow pre-section syntax used by process-resident old plans.

    Legacy compatibility is intentionally limited to lines that start with
    ``Modify`` and contain backtick-delimited repository files. Any plan with a
    structured Modifiable/Frozen section bypasses this fallback completely.
    Revision proposals migrate legacy plans to the structured section format.
    """

    paths: list[str] = []
    seen: set[str] = set()
    for line in plan_markdown.splitlines():
        if not re.match(r"^\s*Modify\b", line, re.IGNORECASE):
            continue
        for raw_path in re.findall(r"`([^`]+)`", line):
            path = _normalize_scope_path(raw_path, heading="legacy Modify")
            if path in seen:
                raise ResearchPlanScopeError(
                    f"Duplicate legacy modifiable path: {path}",
                )
            seen.add(path)
            paths.append(path)
    return frozenset(paths)


def parse_research_plan_scope(plan_markdown: str) -> ResearchPlanScope:
    """Parse the approved and frozen repository files from explicit sections."""

    modifiable_body = _section_body(plan_markdown, "Modifiable Files")
    frozen_body = _section_body(plan_markdown, "Frozen Files")
    structured = modifiable_body is not None or frozen_body is not None

    modifiable_paths = _parse_section_paths(
        modifiable_body,
        heading="Modifiable Files",
    )
    frozen_paths = _parse_section_paths(
        frozen_body,
        heading="Frozen Files",
    )
    legacy_syntax = False
    if not structured:
        modifiable_paths = _legacy_modifiable_paths(plan_markdown)
        legacy_syntax = bool(modifiable_paths)

    overlap = sorted(modifiable_paths & frozen_paths)
    if overlap:
        raise ResearchPlanScopeError(
            "Research plan paths cannot be both modifiable and frozen: "
            + ", ".join(overlap),
        )
    return ResearchPlanScope(
        modifiable_paths=modifiable_paths,
        frozen_paths=frozen_paths,
        legacy_syntax=legacy_syntax,
    )


def approved_plan_paths(plan_markdown: str) -> set[str]:
    """Return the exact files approved for modification."""

    return set(parse_research_plan_scope(plan_markdown).modifiable_paths)


def frozen_plan_paths(plan_markdown: str) -> set[str]:
    """Return the exact files that the plan explicitly freezes."""

    return set(parse_research_plan_scope(plan_markdown).frozen_paths)


def _replace_or_append_modifiable_section(
    plan_markdown: str,
    paths: set[str],
) -> str:
    lines = "\n".join(f"- `{path}`" for path in sorted(paths))
    replacement = f"## Modifiable Files\n\n{lines}"
    pattern = re.compile(
        _SECTION_RE_TEMPLATE.format(heading=re.escape("Modifiable Files")),
    )
    if pattern.search(plan_markdown):
        return pattern.sub(replacement + "\n\n", plan_markdown, count=1).rstrip() + "\n"
    return f"{plan_markdown.rstrip()}\n\n{replacement}\n"


def build_plan_revision_proposal(
    dialog: Any,
    instruction: str = "",
) -> tuple[str, str]:
    """Build a non-binding plan revision while preserving scope semantics."""

    plan = (dialog.plan_markdown or "").rstrip()
    requested = instruction.strip()
    if requested:
        section = (
            "## Requested Revision\n\n"
            f"{requested}\n\n"
            "Apply this request while preserving the existing acceptance, "
            "environment, and validation constraints."
        )
        return f"{plan}\n\n{section}\n", "根据聊天中的修改要求生成"

    scope = parse_research_plan_scope(plan)
    missing_paths = {
        _normalize_scope_path(path, heading="Proposed Scope Revision")
        for path in dialog.unapproved_paths
        if path not in scope.modifiable_paths
    }
    if missing_paths:
        proposed = _replace_or_append_modifiable_section(
            plan,
            set(scope.modifiable_paths) | missing_paths,
        )
        return proposed, "根据未审批变更路径生成"

    feedback = (
        dialog.verification_summary
        or dialog.reproduction_summary
        or dialog.error
        or "Review the preserved validation report and address its findings."
    )
    section = (
        "## Proposed Validation Follow-up\n\n"
        f"- Address the preserved validation feedback: {feedback}\n"
        "- Keep the existing file scope unless the user explicitly approves "
        "a scope change.\n"
        "- Re-run the baseline reproduction and candidate verification."
    )
    return f"{plan}\n\n{section}\n", "根据最近一次验证反馈生成"


def install_research_scope_policy(research_module: ModuleType) -> None:
    """Install the extracted policy into the existing research router module."""

    research_module._parse_research_plan_scope = parse_research_plan_scope
    research_module._approved_plan_paths = approved_plan_paths
    research_module._frozen_plan_paths = frozen_plan_paths
    research_module._build_plan_revision_proposal = build_plan_revision_proposal
