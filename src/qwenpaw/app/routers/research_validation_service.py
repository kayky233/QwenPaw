"""Pure validation helpers extracted from the AutoResearch router."""

from __future__ import annotations

import re
from pathlib import Path, PurePosixPath
from types import ModuleType

_IGNORED_RESEARCH_RUNTIME_PATHS = frozenset(
    {
        ".skill.json.lock",
        "skill.json",
    },
)
_NODE_TEST_SUFFIXES = (".test.ts", ".test.tsx", ".test.js", ".test.jsx")


def changed_paths_from_porcelain(status: str) -> list[str]:
    """Parse exact repository paths from Git porcelain output."""

    entries = status.split("\0") if "\0" in status else status.splitlines()
    paths: list[str] = []
    for entry in entries:
        if len(entry) <= 2:
            continue
        # The router process helper strips surrounding whitespace from the full
        # output, which can remove the first line's leading worktree column.
        prefix_length = 2 if entry[1] == " " and entry[2] != " " else 3
        path = entry[prefix_length:].strip()
        if not path:
            continue
        if " -> " in path:
            path = path.rsplit(" -> ", 1)[1]
        paths.append(path.strip('"'))
    return paths


def expanded_changed_paths(worktree: Path, paths: list[str]) -> list[str]:
    """Expand aggregate untracked-directory entries into exact files."""

    expanded: list[str] = []
    for path in paths:
        candidate = worktree / path
        if path.endswith("/") and candidate.is_dir():
            expanded.extend(
                child.relative_to(worktree).as_posix()
                for child in sorted(candidate.rglob("*"))
                if child.is_file()
            )
            continue
        expanded.append(path)
    return expanded


def is_ignored_research_runtime_path(path: str) -> bool:
    """Return whether a changed path is generated AutoResearch metadata."""

    normalized = PurePosixPath(path).as_posix()
    return (
        normalized in _IGNORED_RESEARCH_RUNTIME_PATHS
        or normalized.startswith(".codegraph/")
        or normalized.startswith("research/")
    )


def focused_test_paths(changed_paths: list[str]) -> list[str]:
    """Select changed tests eligible for baseline/candidate comparison."""

    return [
        path
        for path in changed_paths
        if (path.startswith("tests/") and path.endswith(".py"))
        or path.endswith(_NODE_TEST_SUFFIXES)
    ]


def reproduction_baseline_ref(plan_markdown: str) -> str:
    """Read and validate the Git ref used for baseline reproduction."""

    match = re.search(
        r"(?im)^\s*Baseline Ref:\s*`?([^`\r\n]+?)`?\s*$",
        plan_markdown,
    )
    baseline_ref = match.group(1).strip() if match else "HEAD"
    invalid = (
        not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,199}", baseline_ref)
        or baseline_ref.startswith("-")
        or ".." in baseline_ref
        or "@{" in baseline_ref
        or "//" in baseline_ref
        or baseline_ref.endswith(("/", ".", ".lock"))
    )
    if invalid:
        raise RuntimeError(f"Invalid reproduction baseline ref: {baseline_ref!r}")
    return baseline_ref


def plan_markdown_section(plan_markdown: str, heading: str) -> str:
    """Return one Markdown section body for validation reports."""

    match = re.search(
        rf"(?ims)^##\s+{re.escape(heading)}\s*$\n(.*?)(?=^##\s+|\Z)",
        plan_markdown,
    )
    return match.group(1).strip() if match else "Not specified in the approved plan."


def install_research_validation_service(research_module: ModuleType) -> None:
    """Install extracted validation helpers on the legacy router module."""

    research_module._changed_paths_from_porcelain = changed_paths_from_porcelain
    research_module._expanded_changed_paths = expanded_changed_paths
    research_module._is_ignored_research_runtime_path = (
        is_ignored_research_runtime_path
    )
    research_module._focused_test_paths = focused_test_paths
    research_module._reproduction_baseline_ref = reproduction_baseline_ref
    research_module._plan_markdown_section = plan_markdown_section
